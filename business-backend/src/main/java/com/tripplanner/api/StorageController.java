package com.tripplanner.api;
import com.tripplanner.domain.*;
import java.util.Map;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/api/knowledge/admin/storage")
public class StorageController {
  private final StorageMigrationService migration;private final ObjectStorageService objects;
  public StorageController(StorageMigrationService migration,ObjectStorageService objects){this.migration=migration;this.objects=objects;}
  @GetMapping public Object status(){return objects.configuration();}
  @GetMapping("/migration") public Object preview(@RequestParam(defaultValue="25") int limit){return migration.preview(limit);}
  @PostMapping("/migration/{id}") public Object migrate(@PathVariable long id){return migration.migrate(id);}
}
