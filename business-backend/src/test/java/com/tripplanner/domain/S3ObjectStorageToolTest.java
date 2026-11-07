package com.tripplanner.domain;
import static org.junit.jupiter.api.Assertions.*;
import java.io.*;
import java.net.URI;
import java.nio.file.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.core.env.StandardEnvironment;
import software.amazon.awssdk.auth.credentials.*;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import tools.jackson.databind.json.JsonMapper;

@EnabledIfEnvironmentVariable(named="TEST_S3_ENDPOINT",matches=".+")
class S3ObjectStorageToolTest {
  @TempDir Path root;
  @Test void actualS3SnapshotRestoresToNewPrefixResumesAndEmitsLocationMap()throws Exception {
    String endpoint=System.getenv("TEST_S3_ENDPOINT"),bucket="snapshot-test-"+UUID.randomUUID();
    var config=Map.of("UPLOAD_STORAGE_BACKEND","s3","S3_ENDPOINT",endpoint,"S3_BUCKET",bucket,"S3_PREFIX","original","S3_PATH_STYLE","true",
      "S3_ACCESS_KEY_ID","test","S3_SECRET_ACCESS_KEY","test","S3_ALLOW_HTTP","true","DATASET_ID","0123456789abcdef0123456789abcdef");
    var previous=new HashMap<String,String>();config.forEach((key,value)->{previous.put(key,System.getProperty(key));System.setProperty(key,value);});
    try(var fixture=S3Client.builder().endpointOverride(URI.create(endpoint)).region(Region.US_EAST_1).forcePathStyle(true)
      .credentialsProvider(StaticCredentialsProvider.create(AwsBasicCredentials.create("test","test"))).httpClientBuilder(UrlConnectionHttpClient.builder()).build()){
      fixture.createBucket(b->b.bucket(bucket));byte[] bytes="S3 original and restored bytes".getBytes();
      String hash=ObjectStorageService.digest(new ByteArrayInputStream(bytes),bytes.length);String filename=UUID.randomUUID()+".pdf";
      String origin;
      try(var objects=new ObjectStorageService(new StandardEnvironment())){origin=objects.s3Origin();objects.verifyDataset(config.get("DATASET_ID"),true);objects.put(objects.target(filename),new ByteArrayInputStream(bytes),bytes.length,hash,"application/pdf");}
      Path refs=root.resolve("refs.json"),snapshot=root.resolve("snapshot"),mapping=root.resolve("mapping.json");
      var json=JsonMapper.builder().build();Files.writeString(refs,json.writeValueAsString(List.of(
        Map.of("storage_backend","s3","storage_bucket",bucket,"storage_key","original/"+filename,"storage_origin",origin,"sha256",hash,"media_type","application/pdf"),
        Map.of("storage_backend","s3","storage_bucket",bucket,"storage_key","original/missing.pdf","storage_origin",origin,"sha256","0".repeat(64),"media_type","application/pdf","required",false,"size_bytes",7))));
      ObjectStorageTool.run(new String[]{"storage","export","--refs",refs.toString(),"--directory",snapshot.toString()});
      assertThrows(IllegalArgumentException.class,()->ObjectStorageTool.run(new String[]{"storage","restore","--directory",snapshot.toString()}));
      System.setProperty("S3_PREFIX","restored");
      String[] restore={"storage","restore","--directory",snapshot.toString(),"--mapping-output",mapping.toString()};
      ObjectStorageTool.run(restore);ObjectStorageTool.run(restore);
      var map=json.readTree(Files.readString(mapping));assertEquals(2,map.size());var item=map.get(0);
      try(var objects=new ObjectStorageService(new StandardEnvironment())){
        objects.verify(new ObjectStorageService.Location("s3",bucket,item.path("target_key").asText(),item.path("target_origin").asText()),bytes.length,hash);
        assertNull(objects.stat(new ObjectStorageService.Location("s3",bucket,map.get(1).path("target_key").asText(),map.get(1).path("target_origin").asText())));
        try(var original=objects.open(new ObjectStorageService.Location("s3",bucket,"original/"+filename,origin))){assertArrayEquals(bytes,original.readAllBytes());}
      }
      assertEquals("original/"+filename,item.path("source_key").asText());assertTrue(item.path("target_key").asText().startsWith("restored/"));assertTrue(item.path("target_key").asText().endsWith(filename));
    }finally{config.forEach((key,value)->{if(previous.get(key)==null)System.clearProperty(key);else System.setProperty(key,previous.get(key));});}
  }
}
