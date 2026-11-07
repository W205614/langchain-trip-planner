package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import jakarta.annotation.PreDestroy;
import java.io.*;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.time.Duration;
import java.util.*;
import org.springframework.core.env.Environment;
import org.springframework.core.env.StandardEnvironment;
import org.springframework.core.io.AbstractResource;
import org.springframework.core.io.Resource;
import org.springframework.stereotype.Service;
import software.amazon.awssdk.auth.credentials.*;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.*;

/** Immutable object locations belong to records, not to the current write-backend setting. */
@Service
public class ObjectStorageService implements AutoCloseable {
  public record Location(String backend,String bucket,String key,String origin) {
    public Location(String backend,String bucket,String key){this(backend,bucket,key,"");}
    public Location {
      if(!Set.of("local","s3").contains(backend)||bucket==null||key==null||key.isBlank()
          ||key.length()>1024||key.startsWith("/")||key.contains("\\")
          ||Arrays.stream(key.split("/",-1)).anyMatch(p->p.isBlank()||p.equals(".")||p.equals("..")))
        throw new IllegalArgumentException("Invalid immutable storage location");
      if(backend.equals("local")&&(!bucket.isEmpty()||key.contains("/")))throw new IllegalArgumentException("Invalid local object location");
      if(backend.equals("s3")&&!bucket.matches("[a-zA-Z0-9][a-zA-Z0-9._-]{1,253}"))throw new IllegalArgumentException("Invalid S3 bucket");
      if(origin==null||!origin.matches("[a-f0-9]{64}|"))throw new IllegalArgumentException("Invalid storage origin");
      if(backend.equals("local")&&!origin.isEmpty())throw new IllegalArgumentException("Local storage has no S3 origin");
    }
  }
  public record Stat(long size,String sha256) {}
  private final Path uploads;
  private final String backend,bucket,prefix,endpoint,region,access,secret,session;
  private final boolean pathStyle;
  private S3Client client;
  public ObjectStorageService(Environment env) {
    uploads=Path.of(env.getProperty("UPLOAD_DIR","../backend/data/knowledge_uploads")).toAbsolutePath().normalize();
    backend=env.getProperty("UPLOAD_STORAGE_BACKEND","local");bucket=env.getProperty("S3_BUCKET","");
    prefix=env.getProperty("S3_PREFIX","uploads").replaceAll("^/+|/+$","");
    endpoint=env.getProperty("S3_ENDPOINT","");region=env.getProperty("S3_REGION","us-east-1");
    pathStyle=env.getProperty("S3_PATH_STYLE",Boolean.class,false);
    access=env.getProperty("S3_ACCESS_KEY_ID","");secret=env.getProperty("S3_SECRET_ACCESS_KEY","");session=env.getProperty("S3_SESSION_TOKEN","");
    if(!Set.of("local","s3").contains(backend))throw new IllegalArgumentException("UPLOAD_STORAGE_BACKEND must be local or s3");
    if(!prefix.isEmpty())new Location("s3","validation-bucket",prefix+"/object");
    if(!access.isBlank()!=!secret.isBlank())throw new IllegalArgumentException("Configure both S3 credential fields");
    if(backend.equals("s3"))target("configuration-check");
    if(!endpoint.isBlank()){
      URI uri=URI.create(endpoint);
      if(!Set.of("http","https").contains(uri.getScheme())||uri.getHost()==null||uri.getUserInfo()!=null||uri.getQuery()!=null||uri.getFragment()!=null)
        throw new IllegalArgumentException("Invalid S3_ENDPOINT");
      boolean local=Set.of("localhost","127.0.0.1","::1","s3mock").contains(uri.getHost());
      if(!uri.getScheme().equals("https")&&!local&&!env.getProperty("S3_ALLOW_HTTP",Boolean.class,false))throw new IllegalArgumentException("S3 requires TLS; isolated fixtures may set S3_ALLOW_HTTP=true");
    }
  }
  public static ObjectStorageService local(Path root) {
    var env=new StandardEnvironment();env.getPropertySources().addFirst(new org.springframework.core.env.MapPropertySource("local-storage",Map.of("UPLOAD_DIR",root.toString(),"UPLOAD_STORAGE_BACKEND","local")));
    return new ObjectStorageService(env);
  }
  private synchronized S3Client s3() {
    if(client==null){
      var builder=S3Client.builder().region(Region.of(region)).forcePathStyle(pathStyle)
        .httpClientBuilder(UrlConnectionHttpClient.builder().connectionTimeout(Duration.ofSeconds(3)).socketTimeout(Duration.ofSeconds(15)))
        .overrideConfiguration(c->c.apiCallTimeout(Duration.ofSeconds(30)).apiCallAttemptTimeout(Duration.ofSeconds(25))
          .retryStrategy(software.amazon.awssdk.retries.StandardRetryStrategy.builder().maxAttempts(1).build()));
      if(!endpoint.isBlank())builder.endpointOverride(URI.create(endpoint));
      if(!access.isBlank())builder.credentialsProvider(StaticCredentialsProvider.create(session.isBlank()?AwsBasicCredentials.create(access,secret):AwsSessionCredentials.create(access,secret,session)));
      client=builder.build();
    }
    return client;
  }
  public String backend(){return backend;}
  public String endpointIdentity(){return endpointIdentity(backend);}
  public String endpointIdentity(String requestedBackend){return requestedBackend.equals("local")?"local:"+uploads:endpoint.isBlank()?"aws:"+region:endpoint;}
  public String s3Origin(){
    String canonical;
    if(endpoint.isBlank())canonical="https://s3."+region+".amazonaws.com";
    else{URI uri=URI.create(endpoint);int port=uri.getPort();String authority=uri.getHost().toLowerCase(Locale.ROOT)+(port<0||port==443&&uri.getScheme().equals("https")||port==80&&uri.getScheme().equals("http")?"":":"+port);canonical=uri.getScheme().toLowerCase(Locale.ROOT)+"://"+authority+uri.getPath().replaceAll("/+$","");}
    byte[] bytes=(canonical+"|"+region).getBytes(StandardCharsets.UTF_8);
    try{return digest(new ByteArrayInputStream(bytes),bytes.length);}catch(IOException ex){throw new IllegalStateException(ex);}
  }
  private void requireOrigin(Location location){
    if(location.backend().equals("s3")&&!s3Origin().equals(location.origin()))throw new ApiException(409,"S3 来源尚未登记或与当前部署不符，请使用明确的存储迁移／恢复流程","STORAGE_ORIGIN_MISMATCH");
  }
  public Path localRoot(){return uploads;}
  public Location target(String name){return new Location(backend,backend.equals("s3")?bucket:"",backend.equals("s3")&&!prefix.isBlank()?prefix+"/"+name:name,backend.equals("s3")?s3Origin():"");}
  public static Location location(Map<String,Object> row){
    String stored=Objects.toString(row.get("stored_path"),"").replace('\\','/');
    return new Location(Objects.toString(row.get("storage_backend"),"local"),Objects.toString(row.get("storage_bucket"),""),
      Objects.toString(row.get("storage_key"),"").isBlank()?stored.substring(stored.lastIndexOf('/')+1):row.get("storage_key").toString(),Objects.toString(row.get("storage_origin"),""));
  }
  private Path path(Location location)throws IOException {
    Path target=uploads.resolve(location.key()).normalize();
    if(!target.startsWith(uploads)||Files.isSymbolicLink(uploads)||Files.isSymbolicLink(target))throw new IOException("Unsafe local object path");
    return target;
  }
  public void prepareWrite()throws IOException {if(backend.equals("local")){Files.createDirectories(uploads);if(Files.isSymbolicLink(uploads))throw new IOException("Unsafe local object directory");}}
  public long usableSpace()throws IOException {prepareWrite();return Files.getFileStore(uploads).getUsableSpace();}
  public void put(Location location,InputStream source,long size,String hash,String media)throws IOException {
    requireOrigin(location);
    try(source){
      if(location.backend().equals("local")){
        Files.createDirectories(uploads);
        try(var out=Files.newOutputStream(path(location),StandardOpenOption.CREATE_NEW)){
          byte[] buffer=new byte[8192];long total=0;int read;
          while((read=source.read(buffer))!=-1){total+=read;if(total>size)throw new IOException("Object exceeds declared size");out.write(buffer,0,read);}
          if(total!=size)throw new IOException("Object differs from declared size");
        }
      }else{
        s3().putObject(PutObjectRequest.builder().bucket(location.bucket()).key(location.key()).contentType(media)
          .metadata(Map.of("sha256",hash)).ifNoneMatch("*").build(),RequestBody.fromInputStream(source,size));
        if(source.read()!=-1)throw new IOException("Object exceeds declared size");
      }
    }
  }
  public Stat stat(Location location)throws IOException {
    requireOrigin(location);
    if(location.backend().equals("local")){
      Path target=path(location);if(!Files.exists(target,LinkOption.NOFOLLOW_LINKS))return null;
      if(!Files.isRegularFile(target,LinkOption.NOFOLLOW_LINKS))throw new IOException("Not a regular object");
      return new Stat(Files.size(target),"");
    }
    try{var result=s3().headObject(r->r.bucket(location.bucket()).key(location.key()));return new Stat(result.contentLength(),result.metadata().getOrDefault("sha256",""));}
    catch(S3Exception ex){if(ex.statusCode()==404)return null;throw ex;}
  }
  public InputStream open(Location location)throws IOException {
    requireOrigin(location);
    if(location.backend().equals("local"))return Files.newInputStream(path(location));
    return s3().getObject(r->r.bucket(location.bucket()).key(location.key()));
  }
  public static String digest(InputStream source,long expected)throws IOException {
    try(source){
      MessageDigest digest;try{digest=MessageDigest.getInstance("SHA-256");}catch(NoSuchAlgorithmException ex){throw new IllegalStateException(ex);}
      byte[] bytes=new byte[8192];long total=0;int read;
      while((read=source.read(bytes))!=-1){total+=read;if(total>expected)throw new IOException("Object exceeds expected size");digest.update(bytes,0,read);}
      if(total!=expected)throw new IOException("Object size differs");return HexFormat.of().formatHex(digest.digest());
    }
  }
  public void verify(Location location,long size,String hash)throws IOException {
    Stat state=stat(location);
    if(state==null||state.size()!=size||!digest(open(location),size).equals(hash))throw new IOException("Object checksum verification failed");
  }
  public Resource resource(Location location,long expected)throws IOException {
    Stat state=stat(location);if(state==null)throw new NoSuchFileException("Object not found");
    if(expected>=0&&state.size()!=expected)throw new IOException("Object size differs from business metadata");
    return new AbstractResource(){
      @Override public String getDescription(){return "Private document object";}
      @Override public long contentLength(){return state.size();}
      @Override public InputStream getInputStream()throws IOException{return open(location);}
    };
  }
  public void delete(Location location)throws IOException {
    requireOrigin(location);
    if(location.backend().equals("local"))Files.deleteIfExists(path(location));
    else s3().deleteObject(r->r.bucket(location.bucket()).key(location.key()));
  }
  public void verifyDataset(String identity,boolean initialize)throws IOException {
    if(backend.equals("local")){DeploymentIdentity.verifyMarker(uploads,identity,initialize);return;}
    var marker=target(".dataset-id");var state=stat(marker);
    if(state!=null){
      if(state.size()>256)throw new IllegalStateException("Invalid object storage dataset marker");
      try(var in=open(marker)){if(!new String(in.readAllBytes(),StandardCharsets.UTF_8).strip().equals(identity))throw new IllegalStateException("Object storage belongs to another dataset");}
      return;
    }
    if(!initialize)throw new IllegalStateException("S3 prefix has no verified dataset marker; enroll storage before migration");
    if(!s3().listObjectsV2(r->r.bucket(bucket).prefix(prefix.isBlank()?"":prefix+"/").maxKeys(1)).contents().isEmpty())throw new IllegalStateException("Cannot initialize a nonempty S3 prefix");
    byte[] bytes=(identity+"\n").getBytes(StandardCharsets.UTF_8);
    try{put(marker,new ByteArrayInputStream(bytes),bytes.length,digest(new ByteArrayInputStream(bytes),bytes.length),"text/plain");}
    catch(S3Exception ex){if(ex.statusCode()!=412)throw ex;verifyDataset(identity,false);}
  }
  public Map<String,Object> configuration(){return Map.of("backend",backend,"s3_configured",!bucket.isBlank(),"connectivity_checked",false);}
  @PreDestroy public synchronized void close(){if(client!=null){client.close();client=null;}}
}
