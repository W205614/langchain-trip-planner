package com.tripplanner.domain;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import java.nio.file.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.jdbc.core.JdbcTemplate;

class DeploymentIdentityTest {
  @TempDir Path folder;
  final String id="0123456789abcdef0123456789abcdef";
  @Test void existingUnmarkedFilesCannotBeEnrolledAsAnEmptyDataset()throws Exception{
    Files.writeString(folder.resolve("original.pdf"),"original");
    assertThrows(IllegalStateException.class,()->DeploymentIdentity.verifyMarker(folder,id,true));
    assertEquals("original",Files.readString(folder.resolve("original.pdf")));assertFalse(Files.exists(folder.resolve(".dataset-id")));
  }
  @Test void mismatchIsRejectedWithoutOverwritingMarker()throws Exception{
    DeploymentIdentity.verifyMarker(folder,id,true);
    assertThrows(IllegalStateException.class,()->DeploymentIdentity.verifyMarker(folder,"another-dataset-identity",true));
    assertEquals(id,Files.readString(folder.resolve(".dataset-id")).strip());
    DeploymentIdentity.verifyMarker(folder,id,false);
  }
  @Test void productionMustHaveAnIdentityAndExistingDatabaseNeedsExplicitEnrollment(){
    var db=mock(JdbcTemplate.class);
    assertThrows(IllegalStateException.class,()->new DeploymentIdentity(db,"","production",false,folder.toString()).verify());
    when(db.queryForList("SELECT dataset_id FROM deployment_identity WHERE id=1")).thenReturn(List.of());
    when(db.queryForObject("SELECT (SELECT count(*) FROM trip_records)+(SELECT count(*) FROM knowledge_documents)",Long.class)).thenReturn(1L);
    assertThrows(IllegalStateException.class,()->new DeploymentIdentity(db,id,"production",true,folder.toString()).verify());
    verify(db,never()).update(anyString(),any(Object[].class));
  }
}
