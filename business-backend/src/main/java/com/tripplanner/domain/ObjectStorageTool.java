package com.tripplanner.domain;

import java.io.*;
import java.nio.file.*;
import java.util.*;
import org.springframework.core.env.StandardEnvironment;
import tools.jackson.databind.json.JsonMapper;

/** Offline object snapshot command. Uses the same SDK and storage validation as the server. */
public final class ObjectStorageTool {
  private static final JsonMapper JSON=JsonMapper.builder().build();
  private static Map<String,String> arguments(String[] args){
    var result=new HashMap<String,String>();
    for(int i=2;i<args.length;i+=2){if(i+1>=args.length||!args[i].startsWith("--"))throw new IllegalArgumentException("Expected --name value");result.put(args[i].substring(2),args[i+1]);}
    return result;
  }
  public static void run(String[] args)throws Exception {
    if(args.length<2)throw new IllegalArgumentException("storage export|restore|verify|enroll --directory PATH");
    var options=arguments(args);var env=new StandardEnvironment();
    String dataset=env.getProperty("DATASET_ID","");
    if(!dataset.matches("[a-zA-Z0-9_-]{16,64}"))throw new IllegalArgumentException("DATASET_ID required");
    try(var objects=new ObjectStorageService(env)){
      if(args[1].equals("enroll")){objects.verifyDataset(dataset,true);System.out.println("{\"enrolled\":true}");return;}
      Path directory=Path.of(Objects.requireNonNull(options.get("directory"),"--directory required")).toAbsolutePath().normalize();
      if(Files.isSymbolicLink(directory))throw new IllegalArgumentException("Snapshot directory cannot be a link");
      switch(args[1]){
        case "export"->export(objects,dataset,directory,Path.of(Objects.requireNonNull(options.get("refs"),"--refs required")));
        case "restore"->restore(objects,dataset,directory,Path.of(options.getOrDefault("mapping-output",directory.resolve("object-location-map.json").toString())));
        case "verify"->{var manifest=manifest(directory,dataset);for(var row:manifest.path("objects")){if(row.path("missing").asBoolean()){if(row.path("required").asBoolean())throw new IOException("Required object missing");continue;}Path file=blob(directory,row.path("file").asText());if(!ObjectStorageService.digest(Files.newInputStream(file),row.path("size_bytes").asLong()).equals(row.path("sha256").asText()))throw new IOException("Snapshot checksum differs");}System.out.println("{\"verified\":true}");}
        default->throw new IllegalArgumentException("Unknown storage command");
      }
    }
  }
  private static Path blob(Path directory,String name)throws IOException {
    if(!name.matches("[0-9]{8}\\.blob"))throw new IOException("Invalid snapshot filename");
    Path path=directory.resolve(name);if(Files.isSymbolicLink(path))throw new IOException("Snapshot file cannot be a link");return path;
  }
  private static tools.jackson.databind.JsonNode manifest(Path directory,String dataset)throws IOException {
    var value=JSON.readTree(Files.readString(directory.resolve("manifest.json")));
    if(!value.path("schema").asText().equals("trip-object-snapshot/v1")||!value.path("dataset_id").asText().equals(dataset)||!value.path("objects").isArray())throw new IOException("Snapshot identity/schema differs");return value;
  }
  private static ObjectStorageService.Location restoreTarget(ObjectStorageService objects,ObjectStorageService.Location from)throws IOException {
    String name=from.key().substring(from.key().lastIndexOf('/')+1);
    if(from.backend().equals("s3")){
      byte[] identity=(from.origin()+":"+from.bucket()+":"+from.key()).getBytes(java.nio.charset.StandardCharsets.UTF_8);
      name=ObjectStorageService.digest(new ByteArrayInputStream(identity),identity.length).substring(0,16)+"-"+name;
    }
    return objects.target(name);
  }
  private static boolean sameObjectLocation(ObjectStorageService.Location a,ObjectStorageService.Location b){return a.backend().equals(b.backend())&&a.bucket().equals(b.bucket())&&a.key().equals(b.key());}
  private static void export(ObjectStorageService objects,String dataset,Path directory,Path refs)throws Exception {
    var input=JSON.readTree(Files.readString(refs));if(!input.isArray())throw new IllegalArgumentException("Refs must be a JSON array");
    Files.createDirectories(directory);var entries=JSON.createArrayNode();var seen=new HashSet<ObjectStorageService.Location>();
    for(var row:input){
      var location=new ObjectStorageService.Location(row.path("storage_backend").asText(),row.path("storage_bucket").asText(),row.path("storage_key").asText(),row.path("storage_origin").asText());
      if(!seen.add(location))throw new IllegalArgumentException("Duplicate storage reference");
      String hash=row.path("sha256").asText();if(!hash.matches("[a-f0-9]{64}"))throw new IOException("Missing source checksum");
      boolean required=row.path("required").asBoolean(true);
      var state=objects.stat(location);
      if(state==null){
        if(required)throw new IOException("Required referenced object missing");
        entries.addObject().put("storage_backend",location.backend()).put("storage_bucket",location.bucket()).put("storage_key",location.key())
          .put("storage_origin",location.origin())
          .put("sha256",row.path("sha256").asText()).put("size_bytes",row.path("size_bytes").asLong()).put("media_type",row.path("media_type").asText("application/octet-stream")).put("missing",true).put("required",false);
        continue;
      }
      if(state.size()<1||state.size()>20L*1024*1024)throw new IOException("Referenced upload exceeds size contract");
      String filename=String.format(Locale.ROOT,"%08d.blob",entries.size());Path output=blob(directory,filename);
      if(!Files.exists(output)){
        Path temporary=directory.resolve(UUID.randomUUID()+".partial");
        try(var source=objects.open(location);var target=Files.newOutputStream(temporary,StandardOpenOption.CREATE_NEW)){source.transferTo(target);}
        if(!ObjectStorageService.digest(Files.newInputStream(temporary),state.size()).equals(hash))throw new IOException("Export checksum differs");
        Files.move(temporary,output,StandardCopyOption.ATOMIC_MOVE);
      }
      if(!ObjectStorageService.digest(Files.newInputStream(output),state.size()).equals(hash))throw new IOException("Existing snapshot object differs");
      entries.addObject().put("storage_backend",location.backend()).put("storage_bucket",location.bucket()).put("storage_key",location.key())
        .put("storage_origin",location.origin())
        .put("sha256",hash).put("size_bytes",state.size()).put("media_type",row.path("media_type").asText("application/octet-stream")).put("file",filename).put("required",required).put("missing",false);
    }
    var manifest=JSON.createObjectNode().put("schema","trip-object-snapshot/v1").put("dataset_id",dataset);
    manifest.putObject("source_endpoints").put("local",objects.endpointIdentity("local")).put("s3",objects.endpointIdentity("s3"));manifest.set("objects",entries);
    Path target=directory.resolve("manifest.json");String encoded=JSON.writeValueAsString(manifest);
    if(Files.exists(target)){if(!JSON.readTree(Files.readString(target)).equals(manifest))throw new IOException("Snapshot manifest differs; use a new directory");}
    else Files.writeString(target,encoded,StandardOpenOption.CREATE_NEW);
    int missing=0;for(var entry:entries)if(entry.path("missing").asBoolean())missing++;
    System.out.println("{\"exported\":"+(entries.size()-missing)+",\"missing_optional\":"+missing+"}");
  }
  private static void restore(ObjectStorageService objects,String dataset,Path directory,Path mappingOutput)throws Exception {
    var manifest=manifest(directory,dataset);var mapping=JSON.createArrayNode();
    for(var row:manifest.path("objects")){
      var from=new ObjectStorageService.Location(row.path("storage_backend").asText(),row.path("storage_bucket").asText(),row.path("storage_key").asText(),row.path("storage_origin").asText());
      var direct=objects.target(from.key().substring(from.key().lastIndexOf('/')+1));
      if(sameObjectLocation(from,direct)&&(from.backend().equals("s3")||manifest.path("source_endpoints").path("local").asText().equals(objects.endpointIdentity())))throw new IllegalArgumentException("Restore requires a different bucket/prefix or local directory");
    }
    objects.verifyDataset(dataset,true);
    for(var row:manifest.path("objects")){
      var from=new ObjectStorageService.Location(row.path("storage_backend").asText(),row.path("storage_bucket").asText(),row.path("storage_key").asText(),row.path("storage_origin").asText());
      var to=restoreTarget(objects,from);
      if(row.path("missing").asBoolean()){
        if(row.path("required").asBoolean())throw new IOException("Required object missing");
      }else{
        Path file=blob(directory,row.path("file").asText());long size=row.path("size_bytes").asLong();String hash=row.path("sha256").asText();
        if(!ObjectStorageService.digest(Files.newInputStream(file),size).equals(hash))throw new IOException("Snapshot checksum differs");
        if(objects.stat(to)==null)objects.put(to,Files.newInputStream(file),size,hash,row.path("media_type").asText());
        objects.verify(to,size,hash);
      }
      mapping.addObject().put("source_backend",from.backend()).put("source_bucket",from.bucket()).put("source_key",from.key())
        .put("source_origin",from.origin()).put("target_backend",to.backend()).put("target_bucket",to.bucket()).put("target_key",to.key()).put("target_origin",to.origin());
    }
    var target=mappingOutput.toAbsolutePath().normalize();String encoded=JSON.writeValueAsString(mapping);
    if(Files.isSymbolicLink(target)||Files.isSymbolicLink(target.getParent()))throw new IOException("Mapping output cannot be a link");
    if(Files.exists(target)&&!JSON.readTree(Files.readString(target)).equals(mapping))throw new IOException("Existing restore mapping differs");
    if(!Files.exists(target))Files.writeString(target,encoded,StandardOpenOption.CREATE_NEW);
    int missing=0;for(var entry:manifest.path("objects"))if(entry.path("missing").asBoolean())missing++;
    System.out.println("{\"restored\":"+(mapping.size()-missing)+",\"missing_optional\":"+missing+",\"database_references_updated\":false}");
  }
}
