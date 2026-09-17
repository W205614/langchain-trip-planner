package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import com.tripplanner.persistence.KnowledgeMapper;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.multipart.MultipartFile;
import tools.jackson.databind.node.ObjectNode;

@Service
public class KnowledgeService {
  private final KnowledgeMapper documents;
  private final TransactionTemplate tx;
  private final Path uploads;
  private final BusinessAuditService audit;

  public KnowledgeService(
      KnowledgeMapper documents,
      TransactionTemplate tx,
      BusinessAuditService audit,
      @Value("${UPLOAD_DIR:../backend/data/knowledge_uploads}") String uploads) {
    this.documents = documents;
    this.tx = tx;
    this.audit = audit;
    this.uploads = Path.of(uploads).toAbsolutePath().normalize();
  }

  public Map<String, Object> get(long id) {
    var d = documents.get(id);
    if (d == null) throw new ApiException(404, "资料不存在");
    return d;
  }

  public Map<String, Object> serialize(Map<String, Object> row) {
    var r = new LinkedHashMap<String, Object>();
    for (String f :
        List.of(
            "id",
            "city",
            "title",
            "original_filename",
            "status",
            "source_tier",
            "review_note",
            "page_count",
            "version",
            "submitted_by",
            "reviewed_by",
            "created_at",
            "updated_at")) r.put(f, row.get(f));
    return r;
  }

  public Path file(Map<String, Object> document) {
    String stored = document.get("stored_path").toString().replace('\\', '/');
    String name = stored.substring(stored.lastIndexOf('/') + 1);
    Path path = uploads.resolve(name).normalize();
    if (!path.startsWith(uploads)
        || name.isBlank()
        || !Files.isRegularFile(path)
        || Files.isSymbolicLink(path)) throw new ApiException(404, "原文件不存在");
    return path;
  }

  public void removeDeletedOriginal(Map<String, Object> document) throws java.io.IOException {
    if (!"deleted".equals(document.get("status")))
      throw new IllegalArgumentException("Only deleted documents can be unlinked");
    try {
      Files.deleteIfExists(file(document));
    } catch (ApiException ex) {
      if (ex.status != 404) throw ex;
    }
  }

  public Map<String, Object> upload(long uid, String city, String title, MultipartFile file) {
    if (city.isBlank() || city.length() > 64 || title.isBlank() || title.length() > 160)
      throw new ApiException(422, "城市或标题无效");
    if (file.isEmpty()) throw new ApiException(400, "上传文件不能为空");
    if (file.getSize() > 20 * 1024 * 1024) throw new ApiException(413, "文件不能超过 20 MB");
    try {
      byte[] content = file.getBytes();
      String type = detect(content);
      String name =
          UUID.randomUUID()
              + switch (type) {
                case "application/pdf" -> ".pdf";
                case "image/png" -> ".png";
                case "image/jpeg" -> ".jpg";
                case "image/gif" -> ".gif";
                default -> ".webp";
              };
      Files.createDirectories(uploads);
      Files.write(uploads.resolve(name), content, StandardOpenOption.CREATE_NEW);
      var row = new HashMap<String, Object>();
      row.put("submitted_by", uid);
      row.put("city", city.strip());
      row.put("title", title.strip());
      String original = Objects.toString(file.getOriginalFilename(), name).replace('\\', '/');
      original = original.substring(original.lastIndexOf('/') + 1);
      if (original.length() > 255) original = original.substring(0, 255);
      row.put("original_filename", original);
      row.put("stored_path", name);
      row.put("media_type", type);
      row.put(
          "sha256", HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content)));
      // On uncertain commit retain the file; a later orphan audit may safely reconcile it.
      documents.insert(row);
      return serialize(get(((Number) row.get("id")).longValue()));
    } catch (ApiException ex) {
      throw ex;
    } catch (Exception ex) {
      throw new ApiException(500, "资料保存失败");
    }
  }

  private static String detect(byte[] b) {
    if (starts(b, new byte[] {37, 80, 68, 70, 45})) return "application/pdf";
    if (starts(b, new byte[] {(byte) 255, (byte) 216, (byte) 255})) return "image/jpeg";
    if (starts(b, new byte[] {(byte) 137, 80, 78, 71, 13, 10, 26, 10})) return "image/png";
    if (starts(b, "GIF87a".getBytes()) || starts(b, "GIF89a".getBytes())) return "image/gif";
    if (b.length >= 12
        && starts(b, "RIFF".getBytes())
        && new String(b, 8, 4, java.nio.charset.StandardCharsets.US_ASCII).equals("WEBP"))
      return "image/webp";
    throw new ApiException(415, "仅支持 JPEG、PNG、GIF、WebP 图片或扫描 PDF");
  }

  private static boolean starts(byte[] b, byte[] prefix) {
    return b.length >= prefix.length && Arrays.equals(Arrays.copyOf(b, prefix.length), prefix);
  }

  public Object review(long id, long uid, String action, ObjectNode body, String requestId) {
    return tx.execute(
        s -> {
          var row = get(id);
          int version = ((Number) row.get("version")).intValue();
          String status = row.get("status").toString();
          int next = version + 1;
          boolean enqueue = false;
          switch (action) {
            case "approve" -> {
              if (!Set.of("pending", "rejected", "failed").contains(status))
                throw new ApiException(409, "当前状态不能重复审核");
              row.put("status", "queued");
              enqueue = true;
              String tier = body.path("source_tier").asText("community");
              if (!Set.of("community", "reviewed", "official").contains(tier))
                throw new ApiException(422, "来源等级无效");
              row.put("source_tier", tier);
            }
            case "reject" -> {
              if (Set.of("published", "deleted").contains(status))
                throw new ApiException(409, "已发布资料请使用删除接口");
              row.put("status", "rejected");
            }
            case "delete" -> {
              if (status.equals("deleted")) return Map.of("success", true, "message", "资料已删除");
              row.put("status", "deleted");
              row.put("source_text", "");
              row.put("extracted_pages_json", "[]");
              enqueue = true;
            }
            case "extraction", "publish" -> {
              if (body.path("version").asInt(0) != version || !status.equals("awaiting_review"))
                throw new ApiException(409, "资料已变更，请重新打开复核");
              if (action.equals("extraction")) {
                var pages = body.path("pages");
                if (!pages.isArray()
                    || pages.size() != ((Number) row.get("page_count")).intValue()
                    || pages.isEmpty()
                    || pages.size() > 10) throw new ApiException(422, "请保留原页数");
                for (var p : pages)
                  if (!p.isString() || p.asText("").isBlank() || p.asText("").length() > 12000)
                    throw new ApiException(422, "每页须有内容且不超过12000字");
                row.put("extracted_pages_json", pages.toString());
                row.put(
                    "source_text",
                    String.join("\n\n", pages.valueStream().map(p -> p.asText("")).toList()));
              } else {
                row.put("status", "publishing");
                next = version;
                enqueue = true;
              }
            }
            default -> throw new ApiException(404, "未知资料操作");
          }
          String note = body.path("note").asText("");
          if (note.length() > 500) throw new ApiException(422, "审核备注过长");
          row.put("review_note", note.strip());
          row.put("reviewed_by", uid);
          row.put("next_version", next);
          if (documents.update(row) != 1) throw new ApiException(409, "资料版本冲突");
          if (enqueue) documents.enqueue(id, next, action.equals("approve") ? "parse" : action);
          audit.success(
              uid,
              "knowledge." + action,
              "knowledge_document",
              id,
              next,
              requestId,
              Map.of("status", row.get("status").toString()));
          return Map.of("success", true, "data", serialize(get(id)), "message", "资料状态已更新");
        });
  }
}
