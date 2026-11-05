/*
 * Copyright 2026 AFP Production Studio contributors
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 *
 * ---------------------------------------------------------------------------
 * This adapter links against Alpheus AFP Parser, which is GPL-3.0-or-later.
 * It therefore contains no iText code and never touches the PDF handler
 * registry: the iText 9 artifacts Alpheus depends on are AGPL licensed and
 * are not needed to parse AFP.
 */
package com.studio.afp;

import com.mgz.afp.base.StructuredField;
import com.mgz.afp.base.StructuredFieldBaseName;
import com.mgz.afp.base.StructuredFieldErrornouslyBuilt;
import com.mgz.afp.enums.AFPUnitBase;
import com.mgz.afp.enums.SFTypeID;
import com.mgz.afp.modca.MDD_MediumDescriptor;
import com.mgz.afp.modca.MMC_MediumModificationControl;
import com.mgz.afp.modca.MMC_MediumModificationControl.MMC_KeyWord.MMC_KeyWordID;
import com.mgz.afp.parser.AFPParser;
import com.mgz.afp.parser.AFPParserConfiguration;

import java.io.File;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.ArrayList;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Reads one AFP file with Alpheus and writes the result as JSON on standard
 * output, using the versioned contract described in docs/JSON_CONTRACT.md.
 *
 * The adapter is deliberately small: it answers the questions Studio needs
 * to cross-check the Python parser — how many pages there are, where they
 * start, which medium maps exist and which ones are invoked — and it keeps
 * the byte offset of everything it reports.
 */
public final class AdapterMain {

  /** The contract version this adapter speaks. */
  public static final String CONTRACT_VERSION = "1.0.0";

  /** The adapter's own version. */
  public static final String ADAPTER_VERSION = "2.0.0";

  /** The Alpheus commit this adapter was compiled against. */
  public static final String ALPHEUS_COMMIT = "b2261a78c2254c32ab136c66199a9f86471d79f1";

  private static final double MM_PER_INCH_10 = 254.0;
  private static final double MM_PER_CENTIMETRE_10 = 100.0;

  private AdapterMain() {
    // Utility class.
  }

  public static void main(String[] args) {
    String input = null;
    String format = "json";
    for (int i = 0; i < args.length; i++) {
      String arg = args[i];
      switch (arg) {
        case "-h", "--help" -> {
          usage(System.out);
          System.exit(0);
        }
        case "--input", "-i" -> {
          if (i + 1 < args.length) {
            input = args[++i];
          }
        }
        case "--format", "-f" -> {
          if (i + 1 < args.length) {
            format = args[++i];
          }
        }
        default -> {
          if (arg.startsWith("--input=")) {
            input = arg.substring("--input=".length());
          } else if (arg.startsWith("--format=")) {
            format = arg.substring("--format=".length());
          }
        }
      }
    }

    if (input == null) {
      usage(System.err);
      System.exit(2);
    }
    if (!"json".equalsIgnoreCase(format) && !"jsonl".equalsIgnoreCase(format)) {
      System.err.println("Only 'json' and 'jsonl' output formats are supported.");
      System.exit(2);
    }

    File file = new File(input);
    if (!file.isFile()) {
      System.err.println("The input file does not exist: " + input);
      System.exit(2);
    }

    try {
      PrintStream out = new PrintStream(System.out, true, StandardCharsets.UTF_8);
      if ("jsonl".equalsIgnoreCase(format)) {
        out.println("{\"type\":\"header\",\"contract_version\":\"2.0.0\",\"input_size\":"
            + file.length() + ",\"input_sha256\":" + quote(digest(file)) + "}");
        out.print(convert(file, out));
      } else {
        out.print(convert(file, null));
      }
      out.flush();
    } catch (Exception e) {
      System.err.println("The Alpheus adapter failed: " + e);
      System.exit(1);
    }
  }

  private static void usage(PrintStream out) {
    out.println("Usage: java -jar alpheus-adapter.jar --input <file.afp> --format json");
  }

  /** Parse the file and build the JSON document. */
  static String convert(File file, PrintStream stream) throws Exception {
    AFPParserConfiguration config = new AFPParserConfiguration();
    config.setAFPFile(file);
    // A malformed field must not stop the run: the errors are reported and
    // the parse continues, so Studio can compare partial results.
    config.setEscalateParsingErrors(false);

    List<PageRecord> pages = new ArrayList<>();
    List<MapRecord> mediumMaps = new ArrayList<>();
    List<InvokeRecord> invokes = new ArrayList<>();
    List<ErrorRecord> errors = new ArrayList<>();

    long fieldCount = 0;
    int documentCount = 0;
    int currentPage = -1;
    boolean pageOpen = false;
    MessageDigest fieldDigest = MessageDigest.getInstance("SHA-256");
    Deque<MapRecord> openMaps = new ArrayDeque<>();
    String currentFormMap = null;

    AFPParser parser = new AFPParser(config);
    try {
      StructuredField sf;
      while ((sf = parser.parseNextSF()) != null) {
        fieldCount++;
        try {
          var intro = sf.getStructuredFieldIntroducer();
          SFTypeID fieldType = intro.getSFTypeID();
          if (fieldType == SFTypeID.BPG_BeginPage) pageOpen = true;
          if (stream != null) {
            byte[] id = intro.getRawSfTypeID();
            if (id == null) id = fieldType.toBytes();
            int fieldId = (Byte.toUnsignedInt(id[0]) << 16) | (Byte.toUnsignedInt(id[1]) << 8) | Byte.toUnsignedInt(id[2]);
            int pageNumber = fieldType == SFTypeID.BPG_BeginPage ? currentPage + 2 : currentPage + 1;
            String line = "{\"type\":\"field\",\"seq\":" + (fieldCount - 1)
                + ",\"offset\":" + intro.getFileOffset() + ",\"length\":" + (intro.getSFLength() + 1)
                + ",\"sf_id\":" + fieldId + ",\"page\":" + (pageOpen ? Integer.toString(pageNumber) : "null") + "}\n";
            fieldDigest.update(line.getBytes(StandardCharsets.UTF_8));
            stream.print(line);
          }
          if (sf instanceof StructuredFieldErrornouslyBuilt err) {
            errors.add(new ErrorRecord(
                "structured_field_error",
                err.getErrorMessage() == null ? "The field could not be parsed." : err.getErrorMessage(),
                err.getStructuredFieldIntroducer() == null
                    ? null
                    : err.getStructuredFieldIntroducer().getFileOffset()));
            continue;
          }
          SFTypeID type = sf.getStructuredFieldIntroducer().getSFTypeID();
          long offset = sf.getStructuredFieldIntroducer().getFileOffset();
          String name = nameOf(sf);

          switch (type) {
            case BDT_BeginDocument -> documentCount++;
            case BPG_BeginPage -> {
              currentPage++;
              pages.add(new PageRecord(currentPage + 1, offset));
            }
            case EPG_EndPage -> pageOpen = false;
            case BFM_BeginFormMap -> currentFormMap = name;
            case EFM_EndFormMap -> currentFormMap = null;
            case BMM_BeginMediumMap -> {
              MapRecord record = new MapRecord(name, offset, currentFormMap);
              openMaps.push(record);
              mediumMaps.add(record);
            }
            case EMM_EndMediumMap -> { if (!openMaps.isEmpty()) openMaps.pop(); }
            case MDD_MediumDescriptor -> {
              MDD_MediumDescriptor mdd = (MDD_MediumDescriptor) sf;
              MapRecord record = openMaps.peek();
              if (record != null) {
                record.widthMm = toMillimetres(mdd.getxMediumExtent(),
                    mdd.getxUnitBase(), mdd.getxUnitsPerUnitBase());
                record.heightMm = toMillimetres(mdd.getyMediumExtent(),
                    mdd.getyUnitBase(), mdd.getyUnitsPerUnitBase());
              }
            }
            case MMC_MediumModificationControl -> {
              var mmc = (MMC_MediumModificationControl) sf;
              var current = openMaps.peek();
              if (current != null && mmc.getKeywords() != null) {
                for (var keyword : mmc.getKeywords()) {
                  if (keyword.keywordID() == MMC_KeyWordID.DuplexControl) {
                    current.duplexControls.add((int) keyword.parameter() & 0xff);
                  }
                }
              }
            }
            case IMM_InvokeMediumMap -> invokes.add(
                new InvokeRecord(name, offset, pageOpen ? currentPage + 1 : null));
            default -> {
              // Nothing to record.
            }
          }
        } finally {
          sf.release();
        }
      }
    } finally {
      parser.quitParsing();
    }

    long parsedBytes = parser.getCountReadByte();
    boolean complete = parser.getNrOfSFBuiltWithErrors() == 0
        && parsedBytes >= file.length()
        && fieldCount > 0;
    String result = render(file, fieldCount, documentCount, pages, mediumMaps, invokes, errors, complete);
    return stream == null ? result : "{\"type\":\"summary\",\"field_count\":" + fieldCount
        + ",\"fields_sha256\":" + quote(HexFormat.of().formatHex(fieldDigest.digest()))
        + ",\"result\":" + result + "}\n";
  }

  private static String digest(File file) throws Exception {
    MessageDigest digest = MessageDigest.getInstance("SHA-256");
    try (var input = Files.newInputStream(file.toPath())) {
      byte[] buffer = new byte[65536];
      int count;
      while ((count = input.read(buffer)) >= 0) digest.update(buffer, 0, count);
    }
    return HexFormat.of().formatHex(digest.digest());
  }

  private static String nameOf(StructuredField sf) {
    if (sf instanceof StructuredFieldBaseName named) {
      String name = named.getName();
      return name == null ? "" : name.trim();
    }
    return "";
  }

  /** Convert an AFP measurement into millimetres, or null when it cannot be. */
  static Double toMillimetres(int extent, AFPUnitBase base, int unitsPerBase) {
    if (extent <= 0 || unitsPerBase <= 0 || base == null) {
      return null;
    }
    double scale;
    if (base == AFPUnitBase.Inches10) {
      scale = MM_PER_INCH_10;
    } else if (base == AFPUnitBase.Centimeter10) {
      scale = MM_PER_CENTIMETRE_10;
    } else {
      // A logical resolution ratio has no physical size.
      return null;
    }
    return Math.round(extent * scale / unitsPerBase * 100.0) / 100.0;
  }

  private static String render(
      File file,
      long fieldCount,
      int documentCount,
      List<PageRecord> pages,
      List<MapRecord> mediumMaps,
      List<InvokeRecord> invokes,
      List<ErrorRecord> errors,
      boolean complete) {
    StringBuilder sb = new StringBuilder(1 << 16);
    sb.append('{');
    sb.append("\"contract_version\":").append(quote(CONTRACT_VERSION)).append(',');
    sb.append("\"adapter\":{")
        .append("\"name\":\"alpheus-adapter\",")
        .append("\"version\":").append(quote(ADAPTER_VERSION)).append(',')
        .append("\"alpheus_commit\":").append(quote(ALPHEUS_COMMIT))
        .append("},");
    sb.append("\"document\":{")
        .append("\"filename\":").append(quote(file.getName())).append(',')
        .append("\"size_bytes\":").append(file.length()).append(',')
        .append("\"page_count\":").append(pages.size()).append(',')
        .append("\"documents\":").append(documentCount).append(',')
        .append("\"structured_field_count\":").append(fieldCount)
        .append("},");
    sb.append("\"complete\":").append(complete).append(',');

    sb.append("\"pages\":[");
    for (int i = 0; i < pages.size(); i++) {
      PageRecord page = pages.get(i);
      if (i > 0) {
        sb.append(',');
      }
      sb.append("{\"page_number\":").append(page.number)
          .append(",\"offset\":").append(page.offset)
          .append(",\"page_group\":null,\"field_count\":0}");
    }
    sb.append("],");

    sb.append("\"medium_maps\":[");
    for (int i = 0; i < mediumMaps.size(); i++) {
      MapRecord map = mediumMaps.get(i);
      if (i > 0) {
        sb.append(',');
      }
      sb.append("{\"name\":").append(quote(map.name))
          .append(",\"offset\":").append(map.offset)
          .append(",\"form_map\":").append(quote(map.formMap))
          .append(",\"width_mm\":").append(number(map.widthMm))
          .append(",\"height_mm\":").append(number(map.heightMm))
          .append(",\"media_type\":null,\"media_destination\":null,\"duplex\":").append(quote(map.duplex())).append('}');
    }
    sb.append("],");

    sb.append("\"invokes\":[");
    for (int i = 0; i < invokes.size(); i++) {
      InvokeRecord invoke = invokes.get(i);
      if (i > 0) {
        sb.append(',');
      }
      sb.append("{\"name\":").append(quote(invoke.name))
          .append(",\"offset\":").append(invoke.offset)
          .append(",\"page_number\":")
          .append(invoke.pageNumber == null ? "null" : String.valueOf(invoke.pageNumber))
          .append(",\"in_page\":").append(invoke.pageNumber != null)
          .append('}');
    }
    sb.append("],");

    sb.append("\"errors\":[");
    for (int i = 0; i < errors.size(); i++) {
      ErrorRecord error = errors.get(i);
      if (i > 0) {
        sb.append(',');
      }
      sb.append("{\"code\":").append(quote(error.code))
          .append(",\"message\":").append(quote(error.message))
          .append(",\"offset\":")
          .append(error.offset == null ? "null" : String.valueOf(error.offset))
          .append('}');
    }
    sb.append(']');
    sb.append('}');
    return sb.toString();
  }

  private static String number(Double value) {
    return value == null ? "null" : String.valueOf(value);
  }

  /** Quote a string as a JSON string literal. */
  static String quote(String value) {
    if (value == null) {
      return "null";
    }
    StringBuilder sb = new StringBuilder(value.length() + 2);
    sb.append('"');
    for (int i = 0; i < value.length(); i++) {
      char c = value.charAt(i);
      switch (c) {
        case '"' -> sb.append("\\\"");
        case '\\' -> sb.append("\\\\");
        case '\n' -> sb.append("\\n");
        case '\r' -> sb.append("\\r");
        case '\t' -> sb.append("\\t");
        case '\b' -> sb.append("\\b");
        case '\f' -> sb.append("\\f");
        default -> {
          if (c < 0x20 || c == 0x7F) {
            sb.append(String.format("\\u%04x", (int) c));
          } else {
            sb.append(c);
          }
        }
      }
    }
    sb.append('"');
    return sb.toString();
  }

  private static final class PageRecord {
    final int number;
    final long offset;

    PageRecord(int number, long offset) {
      this.number = number;
      this.offset = offset;
    }
  }

  private static final class MapRecord {
    final String name;
    final long offset;
    final String formMap;
    Double widthMm;
    Double heightMm;
    final Set<Integer> duplexControls = new HashSet<>();

    String duplex() {
      if (duplexControls.isEmpty()) return null;
      if (duplexControls.size() != 1) return "conflict";
      int value = duplexControls.iterator().next();
      return switch (value) {
        case 1 -> "simplex";
        case 2 -> "duplex-long-edge";
        case 3 -> "duplex-short-edge";
        default -> String.format("unknown-0x%02X", value);
      };
    }

    MapRecord(String name, long offset, String formMap) {
      this.name = name == null ? "" : name;
      this.offset = offset;
      this.formMap = formMap == null ? "" : formMap;
    }
  }

  private static final class InvokeRecord {
    final String name;
    final long offset;
    final Integer pageNumber;

    InvokeRecord(String name, long offset, Integer pageNumber) {
      this.name = name == null ? "" : name;
      this.offset = offset;
      this.pageNumber = pageNumber;
    }
  }

  private static final class ErrorRecord {
    final String code;
    final String message;
    final Long offset;

    ErrorRecord(String code, String message, Long offset) {
      this.code = code;
      this.message = message;
      this.offset = offset;
    }
  }
}
