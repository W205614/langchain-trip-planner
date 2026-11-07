package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.mock.env.MockEnvironment;

class ObjectStorageServiceTest {
  @TempDir Path root;
  byte[] bytes="private travel document".getBytes(java.nio.charset.StandardCharsets.UTF_8);
  String hash()throws Exception{return ObjectStorageService.digest(new ByteArrayInputStream(bytes),bytes.length);}
  @Test void localLifecycleStreamsVerifiesAndRejectsOverwrite()throws Exception {
    try(var storage=ObjectStorageService.local(root)){
      var location=storage.target(UUID.randomUUID()+".pdf");
      storage.put(location,new ByteArrayInputStream(bytes),bytes.length,hash(),"application/pdf");
      storage.verify(location,bytes.length,hash());
      try(var input=storage.resource(location,bytes.length).getInputStream()){assertArrayEquals(bytes,input.readAllBytes());}
      assertThrows(IOException.class,()->storage.put(location,new ByteArrayInputStream(bytes),bytes.length,hash(),"application/pdf"));
      assertThrows(IOException.class,()->storage.verify(location,bytes.length,"0".repeat(64)));
      storage.delete(location);storage.delete(location);assertNull(storage.stat(location));
    }
  }
  @Test void locationsRejectTraversalAndChangingWriteBackendDoesNotChangeLocalReads()throws Exception {
    assertThrows(IllegalArgumentException.class,()->new ObjectStorageService.Location("local","","../private"));
    assertThrows(IllegalArgumentException.class,()->new ObjectStorageService.Location("s3","bucket","uploads/../private"));
    var row=Map.<String,Object>of("stored_path","/legacy/uploads/old.png");
    assertEquals(new ObjectStorageService.Location("local","","old.png"),ObjectStorageService.location(row));
    try(var local=ObjectStorageService.local(root)){
      var location=local.target("legacy.png");local.put(location,new ByteArrayInputStream(bytes),bytes.length,hash(),"image/png");
      var env=new MockEnvironment().withProperty("UPLOAD_DIR",root.toString()).withProperty("UPLOAD_STORAGE_BACKEND","s3").withProperty("S3_BUCKET","future-bucket");
      try(var changed=new ObjectStorageService(env);var input=changed.open(location)){assertArrayEquals(bytes,input.readAllBytes());}
    }
  }
  @Test void declaredByteBudgetAndDatasetOwnershipAreEnforced()throws Exception {
    try(var storage=ObjectStorageService.local(root)){
      storage.verifyDataset("0123456789abcdef0123456789abcdef",true);
      storage.verifyDataset("0123456789abcdef0123456789abcdef",false);
      assertThrows(IllegalStateException.class,()->storage.verifyDataset("another-dataset-identity",false));
      assertThrows(IOException.class,()->storage.put(storage.target("too-large.pdf"),new ByteArrayInputStream(bytes),3,hash(),"application/pdf"));
      assertEquals(0,storage.stat(storage.target("too-large.pdf")).size());
    }
  }
  @Test void endpointOrRegionChangeRejectsOldOriginsBeforeAnyNetworkCall()throws Exception {
    var env=new MockEnvironment().withProperty("UPLOAD_STORAGE_BACKEND","s3").withProperty("S3_BUCKET","original-bucket").withProperty("S3_ENDPOINT","https://s3.example.invalid");
    ObjectStorageService.Location original;
    try(var first=new ObjectStorageService(env)){original=first.target("original.pdf");}
    env.withProperty("S3_REGION","us-west-2");
    try(var changed=new ObjectStorageService(env)){
      var error=assertThrows(com.tripplanner.api.ApiException.class,()->changed.stat(original));assertEquals("STORAGE_ORIGIN_MISMATCH",error.code);
      assertThrows(com.tripplanner.api.ApiException.class,()->changed.open(original));assertThrows(com.tripplanner.api.ApiException.class,()->changed.delete(original));
    }
    env.withProperty("S3_REGION","us-east-1").withProperty("S3_ENDPOINT","https://other.example.invalid");
    try(var changed=new ObjectStorageService(env)){assertThrows(com.tripplanner.api.ApiException.class,()->changed.stat(original));}
  }
}
