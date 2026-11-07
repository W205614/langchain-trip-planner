package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;
import java.io.*;
import java.net.URI;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.mock.env.MockEnvironment;
import software.amazon.awssdk.auth.credentials.*;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;

@EnabledIfEnvironmentVariable(named="TEST_S3_ENDPOINT",matches=".+")
class S3ObjectStorageTest {
  @Test void actualSdkSupportsPrivateObjectsChecksumsConditionsAndDatasetMarkers()throws Exception {
    String endpoint=System.getenv("TEST_S3_ENDPOINT"),bucket="storage-test-"+UUID.randomUUID();
    try(var fixture=S3Client.builder().endpointOverride(URI.create(endpoint)).region(Region.US_EAST_1).forcePathStyle(true)
      .credentialsProvider(StaticCredentialsProvider.create(AwsBasicCredentials.create("test","test"))).httpClientBuilder(UrlConnectionHttpClient.builder()).build()){
      fixture.createBucket(b->b.bucket(bucket));
      var env=new MockEnvironment().withProperty("UPLOAD_STORAGE_BACKEND","s3").withProperty("S3_ENDPOINT",endpoint)
        .withProperty("S3_BUCKET",bucket).withProperty("S3_PREFIX","private").withProperty("S3_PATH_STYLE","true")
        .withProperty("S3_ACCESS_KEY_ID","test").withProperty("S3_SECRET_ACCESS_KEY","test").withProperty("S3_ALLOW_HTTP","true");
      try(var storage=new ObjectStorageService(env)){
        String identity="0123456789abcdef0123456789abcdef";storage.verifyDataset(identity,true);storage.verifyDataset(identity,false);
        assertThrows(IllegalStateException.class,()->storage.verifyDataset("another-dataset-identity",false));
        byte[] bytes="private S3 original".getBytes();String hash=ObjectStorageService.digest(new ByteArrayInputStream(bytes),bytes.length);
        var location=storage.target("original.pdf");storage.put(location,new ByteArrayInputStream(bytes),bytes.length,hash,"application/pdf");
        assertEquals(hash,storage.stat(location).sha256());storage.verify(location,bytes.length,hash);
        try(var input=storage.resource(location,bytes.length).getInputStream()){assertArrayEquals(bytes,input.readAllBytes());}
        assertThrows(Exception.class,()->storage.put(location,new ByteArrayInputStream(bytes),bytes.length,hash,"application/pdf"));
        storage.delete(location);assertNull(storage.stat(location));
      }
    }
  }
}
