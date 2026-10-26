package com.tripplanner.security;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.util.*;
import javax.crypto.*;
import javax.crypto.spec.*;

/** RFC 6238 SHA-1, 6 digits, 30 seconds; storage encryption uses a separate operator key. */
public final class Totp {
  private static final String ALPHABET="ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  private Totp() {}
  public static String newSecret() {
    byte[] bytes=new byte[20];new SecureRandom().nextBytes(bytes);return base32(bytes);
  }
  static String base32(byte[] input) {
    StringBuilder result=new StringBuilder();int bits=0,value=0;
    for(byte b:input){value=(value<<8)|(b&255);bits+=8;while(bits>=5){bits-=5;result.append(ALPHABET.charAt((value>>bits)&31));}}
    if(bits>0)result.append(ALPHABET.charAt((value<<(5-bits))&31));return result.toString();
  }
  static byte[] decode(String input) {
    var out=new java.io.ByteArrayOutputStream();int bits=0,value=0;
    for(char c:input.toCharArray()){int next=ALPHABET.indexOf(c);if(next<0)throw new IllegalArgumentException("Invalid TOTP seed");
      value=(value<<5)|next;bits+=5;if(bits>=8){bits-=8;out.write((value>>bits)&255);}}
    return out.toByteArray();
  }
  public static String code(String secret,long step) {
    try {
      Mac mac=Mac.getInstance("HmacSHA1");mac.init(new SecretKeySpec(decode(secret),"HmacSHA1"));
      byte[] value=mac.doFinal(ByteBuffer.allocate(8).putLong(step).array());int o=value[value.length-1]&15;
      int number=((value[o]&127)<<24)|((value[o+1]&255)<<16)|((value[o+2]&255)<<8)|(value[o+3]&255);
      return String.format(Locale.ROOT,"%06d",number%1000000);
    }catch(GeneralSecurityException ex){throw new IllegalStateException(ex);}
  }
  public static long verify(String secret,String supplied,long now,long lastStep) {
    if(supplied==null||!supplied.matches("[0-9]{6}"))return -1;
    long current=now/30;
    for(long step=current-1;step<=current+1;step++)if(step>lastStep && MessageDigest.isEqual(code(secret,step).getBytes(StandardCharsets.US_ASCII),supplied.getBytes(StandardCharsets.US_ASCII)))return step;
    return -1;
  }
  public static byte[] key(String encoded) {
    try{byte[] decoded=Base64.getDecoder().decode(encoded);if(decoded.length!=32)throw new IllegalArgumentException();return decoded;}
    catch(Exception ex){throw new com.tripplanner.api.ApiException(503,"管理员二次验证尚未配置加密密钥","MFA_KEY_UNAVAILABLE");}
  }
  public static String encrypt(String secret,String key) {
    try{byte[] nonce=new byte[12];new SecureRandom().nextBytes(nonce);Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
      cipher.init(Cipher.ENCRYPT_MODE,new SecretKeySpec(key(key),"AES"),new GCMParameterSpec(128,nonce));
      cipher.updateAAD("trip-account-totp-v1".getBytes(StandardCharsets.US_ASCII));
      byte[] encrypted=cipher.doFinal(secret.getBytes(StandardCharsets.US_ASCII));
      return Base64.getEncoder().encodeToString(ByteBuffer.allocate(nonce.length+encrypted.length).put(nonce).put(encrypted).array());
    }catch(GeneralSecurityException ex){throw new IllegalStateException(ex);}
  }
  public static String decrypt(String stored,String key) {
    try{byte[] bytes=Base64.getDecoder().decode(stored);Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
      cipher.init(Cipher.DECRYPT_MODE,new SecretKeySpec(key(key),"AES"),new GCMParameterSpec(128,Arrays.copyOf(bytes,12)));
      cipher.updateAAD("trip-account-totp-v1".getBytes(StandardCharsets.US_ASCII));
      return new String(cipher.doFinal(Arrays.copyOfRange(bytes,12,bytes.length)),StandardCharsets.US_ASCII);
    }catch(GeneralSecurityException|IllegalArgumentException ex){throw new com.tripplanner.api.ApiException(503,"二次验证密钥无法读取，请联系管理员","MFA_KEY_UNAVAILABLE");}
  }
}
