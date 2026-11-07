package com.tripplanner;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
public class BusinessApplication {
  public static void main(String[] args) {
    if(args.length>0&&args[0].equals("storage")){
      try{com.tripplanner.domain.ObjectStorageTool.run(args);}
      catch(Exception ex){System.err.println("Storage command failed: "+ex.getClass().getSimpleName());System.exit(1);}
      return;
    }
    SpringApplication.run(BusinessApplication.class, args);
  }
}
