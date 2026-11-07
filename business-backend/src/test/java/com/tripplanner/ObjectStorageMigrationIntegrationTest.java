package com.tripplanner;
import static org.junit.jupiter.api.Assertions.*;
import com.tripplanner.domain.*;
import com.tripplanner.persistence.KnowledgeMapper;
import java.io.*;
import java.net.URI;
import java.nio.file.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.env.MockEnvironment;
import org.springframework.transaction.support.*;
import software.amazon.awssdk.auth.credentials.*;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;

@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.RANDOM_PORT,properties={
  "trip.jwt-secret=integration-secret-only-01234567890123456789","trip.internal-key=integration-internal-only-01234567890123456789",
  "spring.datasource.url=${TEST_DATABASE_URL}","spring.datasource.username=trip","spring.datasource.password=${TEST_DATABASE_PASSWORD:isolated-test-only}","WORKERS_ENABLED=false"})
@EnabledIfEnvironmentVariable(named="TEST_DATABASE_URL",matches=".+")
@EnabledIfEnvironmentVariable(named="TEST_S3_ENDPOINT",matches=".+")
class ObjectStorageMigrationIntegrationTest {
  @Autowired JdbcTemplate db;@Autowired TransactionTemplate tx;@Autowired KnowledgeMapper documents;
  @TempDir Path directory;
  @Test void copySwitchRetainsSourceThenBusinessDeletionCleansBothGenerations()throws Exception {
    long uid=db.queryForObject("INSERT INTO users(username,hashed_password) VALUES(?,'fixture') RETURNING id",Long.class,"storage_"+UUID.randomUUID());
    String endpoint=System.getenv("TEST_S3_ENDPOINT"),bucket="migration-test-"+UUID.randomUUID();
    try(var fixture=S3Client.builder().endpointOverride(URI.create(endpoint)).region(Region.US_EAST_1).forcePathStyle(true)
      .credentialsProvider(StaticCredentialsProvider.create(AwsBasicCredentials.create("test","test"))).httpClientBuilder(UrlConnectionHttpClient.builder()).build()){
      fixture.createBucket(b->b.bucket(bucket));
      var local=new UploadStorage(db,tx,documents,directory.toString(),100000,1L<<40,0,10,100);
      byte[] bytes="real JDBC and S3 SDK integration".getBytes();var original=local.store(uid,"北京","真实存储链路","fixture.pdf","application/pdf",bytes);
      var env=new MockEnvironment().withProperty("UPLOAD_DIR",directory.toString()).withProperty("UPLOAD_STORAGE_BACKEND","s3").withProperty("S3_ENDPOINT",endpoint)
        .withProperty("S3_BUCKET",bucket).withProperty("S3_PREFIX","objects").withProperty("S3_PATH_STYLE","true")
        .withProperty("S3_ACCESS_KEY_ID","test").withProperty("S3_SECRET_ACCESS_KEY","test").withProperty("S3_ALLOW_HTTP","true");
      try(var objects=new ObjectStorageService(env){
        @Override public void put(Location location,InputStream source,long size,String hash,String media)throws IOException{assertFalse(TransactionSynchronizationManager.isActualTransactionActive());super.put(location,source,size,hash,media);}
        @Override public Stat stat(Location location)throws IOException{assertFalse(TransactionSynchronizationManager.isActualTransactionActive());return super.stat(location);}
        @Override public InputStream open(Location location)throws IOException{assertFalse(TransactionSynchronizationManager.isActualTransactionActive());return super.open(location);}
      }){
        var migration=new StorageMigrationService(db,tx,objects);long id=((Number)original.get("id")).longValue();
        migration.migrate(id);var migrated=documents.get(id);assertEquals("s3",migrated.get("storage_backend"));
        assertTrue(Files.isRegularFile(directory.resolve(original.get("stored_path").toString())));
        try(var source=objects.resource(ObjectStorageService.location(migrated),bytes.length).getInputStream()){assertArrayEquals(bytes,source.readAllBytes());}
        assertEquals("already_current",((Map<?,?>)migration.migrate(id)).get("status"));
        var uploader=new UploadStorage(db,tx,documents,directory.toString(),100000,1L<<40,0,10,100,objects);
        var second=uploader.store(uid,"北京","S3新文件","second.pdf","application/pdf","another source".getBytes());assertEquals("s3",second.get("storage_backend"));
        db.update("UPDATE knowledge_documents SET status='deleted' WHERE id=?",id);
        objects.delete(ObjectStorageService.location(migrated));migration.removeRetainedCopies(id);
        assertFalse(Files.exists(directory.resolve(original.get("stored_path").toString())));
        assertNull(objects.stat(ObjectStorageService.location(migrated)));
        assertEquals(0,db.queryForObject("SELECT count(*) FROM storage_migrations WHERE document_id=?",Integer.class,id));
      }
    }
  }
}
