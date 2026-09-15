package com.tripplanner.api;

public class ApiException extends RuntimeException {
  public final int status;
  public final String code;

  public ApiException(int status, String message) {
    this(status, message, "");
  }

  public ApiException(int status, String message, String code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}
