package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

import com.tripplanner.api.ApiException;
import com.tripplanner.domain.UploadStorage;
import com.tripplanner.persistence.KnowledgeMapper;
import com.tripplanner.security.*;
import java.net.URI;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.*;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.*;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Uses the dedicated security DB only; no models, map vendors or daily Docker state. */
@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.RANDOM_PORT,properties={
    "trip.jwt-secret=isolated-security-jwt-01234567890123456789",
    "trip.internal-key=isolated-security-internal-01234567890123456789",
    "spring.datasource.url=${TEST_DATABASE_URL}","spring.datasource.username=trip",
    "spring.datasource.password=${TEST_DATABASE_PASSWORD:isolated-test-only}","WORKERS_ENABLED=false",
    "APP_ENV=validation","VALIDATION_ALLOW_FIXTURES=yes","ADMIN_MFA_REQUIRED=true",
    "ACCOUNT_SECURITY_ENCRYPTION_KEY=AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="})
@EnabledIfEnvironmentVariable(named="TEST_DATABASE_URL",matches=".+")
class SecurityOptimizationIntegrationTest {
  @Value("${local.server.port}") int port;
  @Autowired JdbcTemplate db;@Autowired JsonMapper json;@Autowired KnowledgeMapper documents;
  @Autowired TransactionTemplate tx;@Autowired PlatformTransactionManager manager;
  @TempDir Path directory;
  final HttpClient http=HttpClient.newHttpClient();
  String username(){return "sec_"+UUID.randomUUID().toString().substring(0,20);}
  final String password="isolated-password-123";
  HttpResponse<String> request(String method,String path,Object body,String bearer,String cookie,String csrf,boolean browser)throws Exception{
    var builder=HttpRequest.newBuilder(URI.create("http://localhost:"+port+path)).header("Content-Type","application/json");
    if(bearer!=null)builder.header("Authorization","Bearer "+bearer);
    if(cookie!=null)builder.header("Cookie",cookie);
    if(csrf!=null)builder.header("X-CSRF-Token",csrf);
    if(browser)builder.header("X-Auth-Mode","cookie");
    builder.method(method,body==null?HttpRequest.BodyPublishers.noBody():HttpRequest.BodyPublishers.ofString(json.writeValueAsString(body)));
    return http.send(builder.build(),HttpResponse.BodyHandlers.ofString());
  }
  JsonNode body(HttpResponse<String> response){return json.readTree(response.body());}
  HttpResponse<String> register(String name,boolean browser)throws Exception{
    var result=request("POST","/api/auth/register",Map.of("username",name,"password",password),null,null,null,browser);
    assertEquals(200,result.statusCode(),result.body());return result;
  }
  String token(String name)throws Exception{return body(register(name,false)).path("access_token").asText();}
  long uid(String name){return db.queryForObject("SELECT id FROM users WHERE username=?",Long.class,name);}
  Map<String,Object> credentials(String code){return Map.of("password",password,"totp_code",code);}
  @Test void browserCookieIsHttpOnlyCsrfBoundAndLogoutRevokesBearerToo()throws Exception{
    String name=username();var registered=register(name,true);assertFalse(body(registered).has("access_token"));
    String session=registered.headers().allValues("Set-Cookie").stream().filter(c->c.startsWith("trip_session=")).findFirst().orElseThrow();
    assertTrue(session.contains("HttpOnly"));assertTrue(session.contains("SameSite=Strict"));
    String cookie=session.split(";",2)[0],raw=cookie.substring("trip_session=".length());
    String csrf=registered.headers().allValues("Set-Cookie").stream().filter(c->c.startsWith("trip_csrf=")).findFirst().orElseThrow().split(";",2)[0].substring("trip_csrf=".length());
    assertEquals("no-store",registered.headers().firstValue("Cache-Control").orElse(""));
    assertEquals(200,request("GET","/api/auth/me",null,null,cookie,null,false).statusCode());
    assertEquals(403,request("POST","/api/auth/logout",Map.of(),null,cookie,null,false).statusCode());
    assertEquals(403,request("POST","/api/auth/logout",Map.of(),null,cookie,"wrong",false).statusCode());
    assertEquals(200,request("GET","/api/auth/me",null,raw,null,null,false).statusCode());
    assertEquals(200,request("POST","/api/auth/logout",Map.of(),null,cookie,csrf,false).statusCode());
    assertEquals(401,request("GET","/api/auth/me",null,raw,null,null,false).statusCode());
    assertEquals(401,request("GET","/api/auth/me",null,null,cookie,null,false).statusCode());
    // An expired/revoked browser cookie must not prevent an explicit fresh login.
    assertEquals(200,request("POST","/api/auth/login",Map.of("username",name,"password",password),null,cookie,null,true).statusCode());
  }
  @Test void passwordChangeAndOneTimeRecoveryRevokeEveryOldSession()throws Exception{
    String name=username(),token=token(name);
    var generated=request("POST","/api/auth/recovery-code",credentials(""),token,null,null,false);
    assertEquals(200,generated.statusCode(),generated.body());String code=body(generated).path("recovery_code").asText();
    assertNotEquals(code,db.queryForObject("SELECT recovery_hash FROM account_security WHERE user_id=?",String.class,uid(name)));
    var changed=request("POST","/api/auth/password",Map.of("current_password",password,"new_password","changed-password-456"),token,null,null,false);
    assertEquals(200,changed.statusCode(),changed.body());assertEquals(401,request("GET","/api/auth/me",null,token,null,null,false).statusCode());
    var login=request("POST","/api/auth/login",Map.of("username",name,"password","changed-password-456"),null,null,null,false);
    assertEquals(200,login.statusCode(),login.body());String changedToken=body(login).path("access_token").asText();
    var recovered=request("POST","/api/auth/recover",Map.of("username",name,"recovery_code",code,"new_password","recovered-password-789"),null,null,null,false);
    assertEquals(200,recovered.statusCode(),recovered.body());assertEquals(401,request("GET","/api/auth/me",null,changedToken,null,null,false).statusCode());
    assertEquals(401,request("POST","/api/auth/recover",Map.of("username",name,"recovery_code",code,"new_password","second-password-789"),null,null,null,false).statusCode());
    assertEquals(200,request("POST","/api/auth/login",Map.of("username",name,"password","recovered-password-789"),null,null,null,false).statusCode());
  }
  @Test void adminMfaMustBeSetThenAllLoginModesRequireNonReplayedCode()throws Exception{
    String name=username(),token=token(name);long uid=uid(name);
    db.update("UPDATE users SET is_admin=true WHERE id=?",uid);
    assertEquals(403,request("GET","/api/knowledge/admin/submissions",null,token,null,null,false).statusCode());
    var enrollment=request("POST","/api/auth/mfa/enroll",credentials(""),token,null,null,false);
    assertEquals(200,enrollment.statusCode(),enrollment.body());String secret=body(enrollment).path("secret").asText();
    assertNotEquals(secret,db.queryForObject("SELECT pending_totp_secret FROM account_security WHERE user_id=?",String.class,uid));
    long step=Instant.now().getEpochSecond()/30;
    assertEquals(200,request("POST","/api/auth/mfa/confirm",Map.of("totp_code",Totp.code(secret,step)),token,null,null,false).statusCode());
    assertEquals(401,request("GET","/api/auth/me",null,token,null,null,false).statusCode());
    var input=new HashMap<String,Object>();input.put("username",name);input.put("password",password);
    assertEquals(401,request("POST","/api/auth/login",input,null,null,null,false).statusCode());
    assertEquals(401,request("POST","/api/auth/login",input,null,null,null,true).statusCode());
    input.put("totp_code",Totp.code(secret,step+1));
    var logged=request("POST","/api/auth/login",input,null,null,null,false);assertEquals(200,logged.statusCode(),logged.body());
    assertEquals(200,request("GET","/api/knowledge/admin/submissions",null,body(logged).path("access_token").asText(),null,null,false).statusCode());
    assertEquals(401,request("POST","/api/auth/login",input,null,null,null,true).statusCode());
  }
  @Test void persistentAccountThrottleSurvivesClientIdentityChanges()throws Exception{
    String name=username();register(name,false);
    for(int i=0;i<5;i++)assertEquals(401,request("POST","/api/auth/login",Map.of("username",name,"password","incorrect"),null,null,null,false).statusCode());
    assertEquals(429,request("POST","/api/auth/login",Map.of("username",name,"password",password),null,null,null,false).statusCode());
    assertTrue(db.queryForObject("SELECT failures FROM account_login_attempts WHERE identity_hash=?",Integer.class,AccountSecurityService.hash(name.toLowerCase(Locale.ROOT)))>=5);
    String other=username();register(other,false);
    assertEquals(200,request("POST","/api/auth/login",Map.of("username",other,"password",password),null,null,null,false).statusCode());
  }
  long existing(){return db.queryForObject("SELECT COALESCE(sum(size_bytes),0) FROM knowledge_documents WHERE status<>'deleted'",Long.class)
      +db.queryForObject("SELECT COALESCE(sum(size_bytes),0) FROM upload_reservations WHERE status IN ('reserved','stored','uncertain')",Long.class);}
  UploadStorage storage(long user,long global,long min,int pending,int rate,KnowledgeMapper mapper,TransactionTemplate template){return new UploadStorage(db,template,mapper,directory.toString(),user,global,min,pending,rate);}
  byte[] content(int seed){byte[] bytes=new byte[80];new Random(seed).nextBytes(bytes);return bytes;}
  Map<String,Object> save(UploadStorage store,long uid,int seed){return store.store(uid,"北京","测试资料","fixture.png","image/png",content(seed));}
  @Test void concurrentUploadReservationsCannotOverspendUserQuota()throws Exception{
    String name=username();register(name,false);long uid=uid(name);var store=storage(200,existing()+10000,0,10,100,documents,tx);
    AtomicInteger accepted=new AtomicInteger(),limited=new AtomicInteger();
    try(var pool=Executors.newFixedThreadPool(4)){
      var calls=new ArrayList<Future<?>>();for(int i=0;i<4;i++){final int seed=i;calls.add(pool.submit(()->{try{save(store,uid,seed);accepted.incrementAndGet();}catch(ApiException ex){assertEquals(429,ex.status);assertEquals("UPLOAD_USER_QUOTA",ex.code);limited.incrementAndGet();}}));}
      for(var call:calls)call.get();
    }
    assertEquals(2,accepted.get());assertEquals(2,limited.get());
    assertEquals(160L,db.queryForObject("SELECT sum(size_bytes) FROM knowledge_documents WHERE submitted_by=?",Long.class,uid));
    try(var files=Files.list(directory)){assertEquals(2,files.count());}
  }
  @Test void streamedUploadNeverRequestsWholeFileBytesAndChangedSourceRemainsUncertain()throws Exception{
    String name=username();register(name,false);long uid=uid(name);
    var store=storage(10000,existing()+10000,0,10,100,documents,tx);
    var streamed=spy(new org.springframework.mock.web.MockMultipartFile("file","fixture.png","image/png",content(10)));
    doThrow(new AssertionError("Whole file buffering is forbidden")).when(streamed).getBytes();
    assertNotNull(store.store(uid,"北京","测试资料","fixture.png","image/png",streamed).get("id"));
    verify(streamed,never()).getBytes();
    var changing=mock(org.springframework.web.multipart.MultipartFile.class);
    when(changing.getSize()).thenReturn(80L);
    when(changing.getInputStream()).thenReturn(new java.io.ByteArrayInputStream(content(11)),new java.io.ByteArrayInputStream(content(12)));
    assertEquals("UPLOAD_RECONCILIATION_REQUIRED",assertThrows(ApiException.class,()->store.store(uid,"北京","变更资料","changed.png","image/png",changing)).code);
    assertEquals(1,db.queryForObject("SELECT count(*) FROM upload_reservations WHERE user_id=? AND status='uncertain'",Integer.class,uid));
    db.update("UPDATE upload_reservations SET updated_at=timezone('UTC',now())-interval '11 minutes' WHERE user_id=? AND status='uncertain'",uid);
    assertEquals(1,((Map<?,?>)store.reconcile()).get("uncertain_retained"));
    try(var files=Files.list(directory)){assertEquals(2,files.count());}
  }
  @Test void duplicateContentIsOwnerScopedAndPendingRateGlobalDiskLimitsAreEffective()throws Exception{
    String name=username();register(name,false);long uid=uid(name);
    var store=storage(10000,existing()+10000,0,1,100,documents,tx);
    var first=save(store,uid,1);assertEquals(first.get("id"),save(store,uid,1).get("id"));
    assertEquals("UPLOAD_DUPLICATE_METADATA",assertThrows(ApiException.class,()->store.store(uid,"上海","不同标题","same.png","image/png",content(1))).code);
    assertEquals("UPLOAD_USER_QUOTA",assertThrows(ApiException.class,()->save(store,uid,2)).code);
    var rate=storage(10000,existing()+10000,0,10,1,documents,tx);
    assertEquals("UPLOAD_RATE_LIMITED",assertThrows(ApiException.class,()->save(rate,uid,2)).code);
    var global=storage(1,existing(),0,10,100,documents,tx);
    // A separate account has no personal usage; the global limit is exactly consumed.
    String other=username();register(other,false);long otherId=uid(other);
    var globalOnly=storage(80,existing(),0,10,100,documents,tx);
    assertEquals("UPLOAD_GLOBAL_QUOTA",assertThrows(ApiException.class,()->save(globalOnly,otherId,3)).code);
    var disk=storage(10000,existing()+10000,Long.MAX_VALUE/2,10,100,documents,tx);
    assertEquals("UPLOAD_LOW_DISK",assertThrows(ApiException.class,()->save(disk,otherId,3)).code);
    assertNotEquals(first.get("id"),save(storage(10000,existing()+10000,0,10,100,documents,tx),otherId,1).get("id"));
  }
  @Test void failedMetadataCommitRetainsFileAndReservationThenReconciles()throws Exception{
    String name=username();register(name,false);long uid=uid(name);KnowledgeMapper failing=mock(KnowledgeMapper.class);
    when(failing.insert(anyMap())).thenThrow(new IllegalStateException("isolated metadata failure"));
    var store=storage(10000,existing()+10000,0,10,100,failing,tx);
    assertEquals("UPLOAD_RECONCILIATION_REQUIRED",assertThrows(ApiException.class,()->save(store,uid,5)).code);
    var row=db.queryForMap("SELECT * FROM upload_reservations WHERE user_id=?",uid);
    assertEquals("uncertain",row.get("status"));assertTrue(Files.exists(directory.resolve(row.get("stored_path").toString())));
    var healthy=storage(10000,existing()+10000,0,10,100,documents,tx);
    assertEquals(409,assertThrows(ApiException.class,()->save(healthy,uid,5)).status);
    db.update("UPDATE upload_reservations SET updated_at=timezone('UTC',now())-interval '11 minutes' WHERE user_id=?",uid);
    var result=(Map<?,?>)healthy.reconcile();assertEquals(1,result.get("recovered"));
    assertEquals("committed",db.queryForObject("SELECT status FROM upload_reservations WHERE user_id=?",String.class,uid));
    assertNotNull(save(healthy,uid,5).get("id"));
  }
  @Test void successfulCommitWithLostAcknowledgmentDoesNotDeleteOrDoubleCharge()throws Exception{
    String name=username();register(name,false);long uid=uid(name);AtomicInteger calls=new AtomicInteger();
    var uncertain=new TransactionTemplate(manager){@Override public <T>T execute(TransactionCallback<T> action){T value=super.execute(action);if(calls.incrementAndGet()==2)throw new IllegalStateException("isolated acknowledgment loss");return value;}};
    var store=storage(10000,existing()+10000,0,10,100,documents,uncertain);
    assertEquals(503,assertThrows(ApiException.class,()->save(store,uid,6)).status);
    assertEquals("committed",db.queryForObject("SELECT status FROM upload_reservations WHERE user_id=?",String.class,uid));
    var healthy=storage(10000,existing()+10000,0,10,100,documents,tx);var result=save(healthy,uid,6);
    assertNotNull(result.get("id"));assertEquals(1,db.queryForObject("SELECT count(*) FROM knowledge_documents WHERE submitted_by=?",Integer.class,uid));
    try(var files=Files.list(directory)){assertEquals(1,files.count());}
  }
  @Test void missingReservationCanBeReleasedButCorruptExistingFileIsRetained()throws Exception{
    String name=username();register(name,false);long uid=uid(name);var healthy=storage(10000,existing()+10000,0,10,100,documents,tx);
    UUID missing=UUID.randomUUID(),corrupt=UUID.randomUUID();
    for(UUID id:List.of(missing,corrupt))db.update("INSERT INTO upload_reservations(id,user_id,sha256,size_bytes,stored_path,city,title,original_filename,media_type,status,updated_at) VALUES(?,?,?,80,?,'北京','待核对','fixture.png','image/png','uncertain',timezone('UTC',now())-interval '11 minutes')",id,uid,AccountSecurityService.hash(id.toString()),id+".png");
    Files.write(directory.resolve(corrupt+".png"),new byte[]{1,2,3});
    var result=(Map<?,?>)healthy.reconcile();assertEquals(1,result.get("released_missing"));assertEquals(1,result.get("uncertain_retained"));
    assertTrue(Files.exists(directory.resolve(corrupt+".png")));assertEquals("uncertain",db.queryForObject("SELECT status FROM upload_reservations WHERE id=?",String.class,corrupt));
  }
}
