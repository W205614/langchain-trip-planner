package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;

import com.tripplanner.api.ApiException;
import com.tripplanner.domain.ObjectStorageService;
import com.tripplanner.domain.UploadStorage;
import com.tripplanner.persistence.KnowledgeMapper;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.nio.file.Path;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.mock.env.MockEnvironment;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
    "trip.jwt-secret=integration-secret-only-01234567890123456789",
    "trip.internal-key=integration-internal-only-01234567890123456789",
    "spring.datasource.url=${TEST_DATABASE_URL}", "spring.datasource.username=trip",
    "spring.datasource.password=${TEST_DATABASE_PASSWORD:isolated-test-only}",
    "UPLOAD_DIR=${java.io.tmpdir}/trip-upload-erasure-validation", "WORKERS_ENABLED=false"})
@EnabledIfEnvironmentVariable(named = "TEST_DATABASE_URL", matches = ".+")
@EnabledIfEnvironmentVariable(named = "TEST_S3_ENDPOINT", matches = ".+")
class UploadErasureUncertaintyIntegrationTest {
  @Autowired JdbcTemplate db;
  @Autowired TransactionTemplate tx;
  @Autowired KnowledgeMapper documents;

  private long user() {
    return db.queryForObject("INSERT INTO users(username,hashed_password) VALUES(?,'fixture') RETURNING id",
        Long.class, "erasure_" + UUID.randomUUID());
  }

  private MockEnvironment fixture() {
    String endpoint = System.getenv("TEST_S3_ENDPOINT"), bucket = "uncertain-" + UUID.randomUUID();
    try (var client = S3Client.builder().endpointOverride(URI.create(endpoint)).region(Region.US_EAST_1)
        .forcePathStyle(true).credentialsProvider(StaticCredentialsProvider.create(AwsBasicCredentials.create("test", "test")))
        .httpClientBuilder(UrlConnectionHttpClient.builder()).build()) {
      client.createBucket(request -> request.bucket(bucket));
    }
    return new MockEnvironment().withProperty("UPLOAD_DIR", Path.of(System.getProperty("java.io.tmpdir")).toString())
        .withProperty("UPLOAD_STORAGE_BACKEND", "s3").withProperty("S3_ENDPOINT", endpoint)
        .withProperty("S3_BUCKET", bucket).withProperty("S3_PREFIX", "originals")
        .withProperty("S3_PATH_STYLE", "true").withProperty("S3_ACCESS_KEY_ID", "test")
        .withProperty("S3_SECRET_ACCESS_KEY", "test").withProperty("S3_ALLOW_HTTP", "true");
  }

  private UploadStorage uploads(ObjectStorageService storage) {
    return new UploadStorage(db, tx, documents, System.getProperty("java.io.tmpdir"),
        100000, 1L << 40, 0, 10, 100, storage);
  }

  @Test void unknownMissingWriteRetainsReservationUntilLateSupplierObjectCanBeDeleted() throws Exception {
    long uid = user();
    var environment = fixture();
    var pending = new AtomicReference<ObjectStorageService.Location>();
    byte[] bytes = "A supplier may publish this after the client lost its response".getBytes();
    try (var uncertain = new ObjectStorageService(environment) {
      @Override public void put(Location location, InputStream input, long size, String hash, String media) throws IOException {
        assertFalse(TransactionSynchronizationManager.isActualTransactionActive());
        pending.set(location);
        try (input) { assertArrayEquals(bytes, input.readAllBytes()); }
        // Inject only the uncertain transport boundary; JDBC/reservation and all
        // HEAD/late PUT/DELETE operations below use their real implementations.
        throw new IOException("Supplier acknowledgement/publication outcome is unknown");
      }
      @Override public Stat stat(Location location) throws IOException {
        assertFalse(TransactionSynchronizationManager.isActualTransactionActive());
        return super.stat(location);
      }
      @Override public void delete(Location location) throws IOException {
        assertFalse(TransactionSynchronizationManager.isActualTransactionActive());
        super.delete(location);
      }
    }; var supplier = new ObjectStorageService(environment)) {
      var storage = uploads(uncertain);
      var failed = assertThrows(ApiException.class,
          () -> storage.store(uid, "北京", "不确定写入", "source.pdf", "application/pdf", bytes));
      assertEquals("UPLOAD_RECONCILIATION_REQUIRED", failed.code);
      var row = db.queryForMap("SELECT * FROM upload_reservations WHERE user_id=?", uid);
      assertEquals("uncertain", row.get("status"));
      assertNotNull(row.get("write_started_at"));
      assertNull(supplier.stat(pending.get()));
      db.update("UPDATE users SET account_state='deleting' WHERE id=?", uid);

      var blocked = assertThrows(ApiException.class, () -> storage.eraseReservations(uid));
      assertEquals("UPLOAD_IN_PROGRESS", blocked.code);
      assertEquals(1, db.queryForObject("SELECT count(*) FROM upload_reservations WHERE user_id=?", Integer.class, uid));
      assertEquals((long) bytes.length, ((Number) ((Map<?, ?>) storage.status(uid, false)).get("reserved_bytes")).longValue());

      supplier.put(pending.get(), new ByteArrayInputStream(bytes), bytes.length,
          ObjectStorageService.digest(new ByteArrayInputStream(bytes), bytes.length), "application/pdf");
      storage.eraseReservations(uid);
      assertNull(supplier.stat(pending.get()));
      assertEquals(0, db.queryForObject("SELECT count(*) FROM upload_reservations WHERE user_id=?", Integer.class, uid));
    }
  }

  @Test void knownCommittedDeletionAndNeverStartedReservationDoNotBlockErasure() throws Exception {
    long uid = user();
    try (var objects = new ObjectStorageService(fixture())) {
      var storage = uploads(objects);
      byte[] bytes = "known completed upload".getBytes();
      var document = storage.store(uid, "北京", "已完成", "complete.pdf", "application/pdf", bytes);
      objects.delete(ObjectStorageService.location(document));
      db.update("UPDATE knowledge_documents SET status='deleted' WHERE id=?", document.get("id"));
      UUID neverStarted = UUID.randomUUID();
      var location = objects.target(neverStarted + ".pdf");
      db.update("INSERT INTO upload_reservations(id,user_id,sha256,size_bytes,stored_path,city,title,original_filename,media_type,status,storage_backend,storage_bucket,storage_key,storage_origin) VALUES(?,?,?,1,?,'北京','尚未开始','pending.pdf','application/pdf','reserved',?,?,?,?)",
          neverStarted, uid, "0".repeat(64), neverStarted + ".pdf", location.backend(), location.bucket(), location.key(), location.origin());
      db.update("UPDATE users SET account_state='deleting' WHERE id=?", uid);
      storage.eraseReservations(uid);
      assertEquals(0, db.queryForObject("SELECT count(*) FROM upload_reservations WHERE user_id=?", Integer.class, uid));
    }
  }
}
