package com.tripplanner.domain;
import static org.junit.jupiter.api.Assertions.*;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.json.JsonMapper;

class ObjectStorageToolTest {
  @TempDir Path temporary;
  @Test void snapshotRoundTripVerifiesBytesAndRefusesOriginalLocation()throws Exception {
    String[] names={"UPLOAD_DIR","UPLOAD_STORAGE_BACKEND","DATASET_ID"};var previous=new HashMap<String,String>();
    for(String name:names)previous.put(name,System.getProperty(name));
    try{
      Path source=temporary.resolve("source"),snapshot=temporary.resolve("snapshot"),target=temporary.resolve("target");
      System.setProperty("UPLOAD_STORAGE_BACKEND","local");System.setProperty("UPLOAD_DIR",source.toString());System.setProperty("DATASET_ID","0123456789abcdef0123456789abcdef");
      byte[] bytes="private source bytes".getBytes();String hash=ObjectStorageService.digest(new ByteArrayInputStream(bytes),bytes.length);
      try(var objects=ObjectStorageService.local(source)){objects.put(objects.target("original.pdf"),new ByteArrayInputStream(bytes),bytes.length,hash,"application/pdf");}
      var refs=temporary.resolve("refs.json");
      Files.writeString(refs,JsonMapper.builder().build().writeValueAsString(List.of(Map.of("storage_backend","local","storage_bucket","","storage_key","original.pdf","sha256",hash,"media_type","application/pdf"))));
      ObjectStorageTool.run(new String[]{"storage","export","--refs",refs.toString(),"--directory",snapshot.toString()});
      ObjectStorageTool.run(new String[]{"storage","verify","--directory",snapshot.toString()});
      assertThrows(IllegalArgumentException.class,()->ObjectStorageTool.run(new String[]{"storage","restore","--directory",snapshot.toString()}));
      System.setProperty("UPLOAD_DIR",target.toString());
      ObjectStorageTool.run(new String[]{"storage","restore","--directory",snapshot.toString()});
      assertArrayEquals(bytes,Files.readAllBytes(target.resolve("original.pdf")));
      assertArrayEquals(bytes,Files.readAllBytes(source.resolve("original.pdf")));
      assertTrue(Files.isRegularFile(snapshot.resolve("object-location-map.json")));
      Files.writeString(snapshot.resolve("00000000.blob"),"corrupted");
      assertThrows(IOException.class,()->ObjectStorageTool.run(new String[]{"storage","verify","--directory",snapshot.toString()}));
    }finally{for(String name:names){if(previous.get(name)==null)System.clearProperty(name);else System.setProperty(name,previous.get(name));}}
  }
}
