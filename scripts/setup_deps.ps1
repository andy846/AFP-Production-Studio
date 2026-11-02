<#
.SYNOPSIS
    Fetches and builds the optional Alpheus Java parser adapter.

.DESCRIPTION
    Studio works on its own. The Alpheus parser is a second, independent
    engine that Studio can use to cross-check the media analysis. This
    script prepares it:

      1. clones Alpheus AFP Parser at a pinned commit,
      2. builds the Alpheus library jar,
      3. compiles the small Studio adapter against it, and
      4. packages both into one self-contained jar.

    The adapter deliberately contains no iText code. Alpheus's PDF
    handler pulls in iText 9, which is AGPL licensed and not needed to
    parse AFP.

    Point Studio at the result with:
        $env:AFP_STUDIO_ALPHEUS_JAR = "<repo>\alpheus-adapter\build\alpheus-adapter.jar"

.PARAMETER SkipAlpheusClone
    Use an Alpheus checkout that is already on disk.

.PARAMETER AlpheusPath
    Where the Alpheus checkout lives (default: third_party\alpheus).
#>

[CmdletBinding()]
param(
    [switch]$SkipAlpheusClone,
    [string]$AlpheusPath = ""
)

$ErrorActionPreference = "Stop"

# The commit the adapter was written against. Changing it is a deliberate
# act: the adapter's behaviour may change with it.
$AlpheusCommit = "b2261a78c2254c32ab136c66199a9f86471d79f1"
$AlpheusUrl = "https://github.com/chatelao/alpheusafpparser"

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $AlpheusPath) {
    $AlpheusPath = Join-Path $RepoRoot "third_party\alpheus"
}
$AdapterDir = Join-Path $RepoRoot "alpheus-adapter"
$AdapterJar = Join-Path $AdapterDir "build\alpheus-adapter.jar"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Resolve-JavaHome {
    if ($env:JAVA_HOME -and (Test-Path (Join-Path $env:JAVA_HOME "bin\javac.exe"))) {
        return $env:JAVA_HOME
    }
    $javac = Get-Command javac -ErrorAction SilentlyContinue
    if ($javac) {
        return (Split-Path -Parent (Split-Path -Parent $javac.Source))
    }
    $bundled = Join-Path $RepoRoot "tools\jdk21"
    if (Test-Path $bundled) {
        $candidate = Get-ChildItem $bundled -Directory -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($candidate) { return $candidate.FullName }
    }
    throw "No JDK was found. Install JDK 21 or set JAVA_HOME."
}

Write-Step "Checking the toolchain"
$JavaHome = Resolve-JavaHome
$Javac = Join-Path $JavaHome "bin\javac.exe"
$Jar = Join-Path $JavaHome "bin\jar.exe"
Write-Host "    JDK: $JavaHome"
& $Javac -version

if (-not $SkipAlpheusClone) {
    Write-Step "Fetching Alpheus at $AlpheusCommit"
    if (-not (Test-Path (Join-Path $AlpheusPath ".git"))) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $AlpheusPath) | Out-Null
        git clone --filter=blob:none $AlpheusUrl $AlpheusPath
    }
    Push-Location $AlpheusPath
    try {
        git fetch --all --tags
        git checkout $AlpheusCommit
    } finally {
        Pop-Location
    }
}

Write-Step "Building the Alpheus library"
Push-Location $AlpheusPath
try {
    $gradlew = if ($IsWindows -or $env:OS -eq "Windows_NT") { ".\gradlew.bat" } else { "./gradlew" }
    & $gradlew --offline jar | Out-Host
    if ($LASTEXITCODE -ne 0) {
        & $gradlew jar | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "The Alpheus build failed." }
    }
} finally {
    Pop-Location
}

$AlpheusJar = Get-ChildItem (Join-Path $AlpheusPath "build\libs") -Filter "alpheusafpparser-*.jar" |
    Where-Object { $_.Name -notmatch "sources|javadoc" } |
    Select-Object -First 1
if (-not $AlpheusJar) {
    throw "The Alpheus jar was not produced in $AlpheusPath\build\libs."
}
Write-Host "    $($AlpheusJar.FullName)"

Write-Step "Compiling the Studio adapter"
$ClassesDir = Join-Path $AdapterDir "build\classes"
$StagingDir = Join-Path $AdapterDir "build\staging"
Remove-Item -LiteralPath $ClassesDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $StagingDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $ClassesDir | Out-Null
New-Item -ItemType Directory -Force -Path $StagingDir | Out-Null

Write-Step "Locating the slf4j runtime"
$Slf4jJar = Get-ChildItem (Join-Path $AlpheusPath ".gradle") -Recurse -Filter "slf4j-api-*.jar" |
    Select-Object -First 1
if (-not $Slf4jJar) {
    # Alpheus declares slf4j as a runtime dependency; look in the shared
    # Gradle cache when the per-project cache does not carry it.
    $GradleCache = Join-Path $env:USERPROFILE ".gradle\caches\modules-2\files-2.1"
    if (Test-Path $GradleCache) {
        $Slf4jJar = Get-ChildItem $GradleCache -Recurse -Filter "slf4j-api-*.jar" |
            Select-Object -First 1
    }
}
if (-not $Slf4jJar) {
    throw "No slf4j-api jar was found. Build Alpheus once online first."
}
Write-Host "    $($Slf4jJar.FullName)"

$CompileClasspath = "$($AlpheusJar.FullName);$($Slf4jJar.FullName)"

$Sources = Get-ChildItem (Join-Path $AdapterDir "src\main\java") -Recurse -Filter "*.java" |
    Select-Object -ExpandProperty FullName
& $Javac -Xlint:-options -source 21 -target 21 -cp $CompileClasspath -d $ClassesDir $Sources
if ($LASTEXITCODE -ne 0) { throw "The adapter did not compile." }

Write-Step "Packaging the adapter jar"
Push-Location $StagingDir
try {
    & $Jar xf $AlpheusJar.FullName
    # The Alpheus library jar does not bundle slf4j; add it so the
    # self-contained adapter runs without a separate classpath.
    $Slf4jClasses = Join-Path $StagingDir "slf4j"
    New-Item -ItemType Directory -Force -Path $Slf4jClasses | Out-Null
    Push-Location $Slf4jClasses
    try {
        & $Jar xf $Slf4jJar.FullName
        Remove-Item -LiteralPath (Join-Path $Slf4jClasses "META-INF") -Recurse -Force -ErrorAction SilentlyContinue
    } finally {
        Pop-Location
    }
    Copy-Item -Path (Join-Path $ClassesDir "*") -Destination $StagingDir -Recurse -Force
    Remove-Item -LiteralPath (Join-Path $StagingDir "META-INF\MANIFEST.MF") -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $AdapterJar) | Out-Null
    & $Jar cfe $AdapterJar com.studio.afp.AdapterMain -C $StagingDir .
    if ($LASTEXITCODE -ne 0) { throw "The adapter jar could not be packaged." }
} finally {
    Pop-Location
}

Write-Step "Done"
Write-Host "    $AdapterJar"
Write-Host ""
Write-Host "Set the environment variable so Studio finds it:" -ForegroundColor Yellow
Write-Host "    `$env:AFP_STUDIO_ALPHEUS_JAR = `"$AdapterJar`""
