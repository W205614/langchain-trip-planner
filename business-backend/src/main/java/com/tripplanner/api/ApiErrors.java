package com.tripplanner.api;

import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
public class ApiErrors {
  @ExceptionHandler(org.springframework.web.multipart.MaxUploadSizeExceededException.class)
  ResponseEntity<?> oversized(Exception ex) {
    return business(new ApiException(413, "文件不能超过 20 MB"));
  }

  @ExceptionHandler(ApiException.class)
  ResponseEntity<?> business(ApiException ex) {
    return ResponseEntity.status(ex.status)
        .body(
            Map.of(
                "success",
                false,
                "detail",
                ex.getMessage(),
                "message",
                ex.getMessage(),
                "error_code",
                ex.code,
                "code",
                ex.code));
  }

  @ExceptionHandler({
    MethodArgumentNotValidException.class,
    MissingRequestHeaderException.class,
    HttpMessageNotReadableException.class,
    org.springframework.web.bind.MissingServletRequestParameterException.class,
    org.springframework.web.method.annotation.MethodArgumentTypeMismatchException.class,
    jakarta.validation.ConstraintViolationException.class
  })
  ResponseEntity<?> validation(Exception ex) {
    return business(new ApiException(422, "请求参数无效"));
  }
}
