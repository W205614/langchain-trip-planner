import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import com.tripplanner.domain.PlanningInputService;
import com.tripplanner.domain.ScheduleDependencies;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

public class ProjectionReview {
 static final JsonMapper json=JsonMapper.builder().build();
 static void require(boolean value,String why){if(!value)throw new AssertionError(why);}
 static void diff(JsonNode a,JsonNode b,String path,List<String> out){
  if(a.equals(b))return;
  if(a.isObject()&&b.isObject()){
   var names=new TreeSet<String>();names.addAll(a.propertyNames());names.addAll(b.propertyNames());
   for(String name:names)diff(a.path(name),b.path(name),path+"/"+name,out);
  }else if(a.isArray()&&b.isArray()&&a.size()==b.size()){
   for(int i=0;i<a.size();i++)diff(a.get(i),b.get(i),path+"/"+i,out);
  }else out.add(path+"="+a+" -> "+b);
 }
 public static void main(String[] args){
  var rules=new ScheduleDependencies(json);
  var input=PlanningInputService.normalize(json.createObjectNode(),3);
  input.put("day_start","09:00").put("day_end","20:00");
  input.withArray("day_windows").addObject().put("day_index",1).put("start","07:00").put("end","20:00");
  var plan=json.createObjectNode();var days=plan.putArray("days");
  for(int i=0;i<3;i++){
   var day=days.addObject().put("day_index",i).put("date","2026-11-0"+(i+1)).put("city",i==0?"Beijing":"Shanghai");
   day.putArray("attractions").addObject().put("poi_id","p"+i).put("name","Synthetic"+i).put("visit_duration",60);
   day.putArray("meals");
   day.putArray("timeline").addObject().put("activity_id","poi:"+i+":p"+i).put("kind","attraction").put("title","Synthetic"+i)
    .put("start_at","2026-11-0"+(i+1)+"T10:00:00+08:00").put("end_at","2026-11-0"+(i+1)+"T11:00:00+08:00").put("status","confirmed");
   day.putObject("feasibility").put("status","feasible");
  }
  PlanningInputService.stableIds(plan);rules.stamp(plan,input,1,null);
  var changed=input.deepCopy();changed.put("day_end","20:30");
  var quality=json.createObjectNode().put("outcome","complete");
  rules.changed(plan,quality,input,changed);
  var originalStored=plan.deepCopy();
  var db=new JdbcTemplate(){@Override public List<Map<String,Object>> queryForList(String sql,Object... params){
   if(!sql.startsWith("SELECT * FROM trip_commitments"))throw new AssertionError("Unexpected SQL");return List.of();
  }};
  var service=new PlanningInputService(db,json,null,rules);
  Map<String,Object> parent=new LinkedHashMap<>();
  parent.put("id",1L);parent.put("travel_days",3);parent.put("version",1);parent.put("planning_revision",2);
  parent.put("planning_input",json.writeValueAsString(changed));parent.put("plan_json",json.writeValueAsString(plan));parent.put("quality_json",json.writeValueAsString(quality));
  var readParent=service.view(parent);
  // The execution and scope guard use the stored parent. Only selected days are rebound.
  var proposalPlan=plan.deepCopy();service.bindSchedule(proposalPlan,changed,2,Set.of(0,2));
  var proposalQuality=quality.deepCopy().put("assistant_proposal_status","pending");
  Map<String,Object> proposal=new LinkedHashMap<>(parent);proposal.put("id",2L);proposal.put("plan_json",json.writeValueAsString(proposalPlan));proposal.put("quality_json",json.writeValueAsString(proposalQuality));
  var readProposal=service.view(proposal);
  var before=json.readTree(readParent.get("plan_json").toString()).path("days").get(1);
  var after=json.readTree(readProposal.get("plan_json").toString()).path("days").get(1);
  var differences=new ArrayList<String>();diff(before,after,"",differences);
  require(differences.size()==1&&differences.getFirst().startsWith("/feasibility/solver_status="),"Expected only solver_status projection delta");
  require(before.path("schedule_stale").asBoolean()&&after.path("schedule_stale").asBoolean(),"Both stale");
  require(before.path("feasibility").path("status").asText().equals("unverified")&&after.path("feasibility").path("status").asText().equals("unverified"),"Both unverified");
  require(originalStored.equals(plan),"view must not mutate stored source");
  var normalizedBefore=((ObjectNode)before).deepCopy();((ObjectNode)normalizedBefore.path("feasibility")).put("solver_status","same-derived-reason");
  var normalizedAfter=((ObjectNode)after).deepCopy();((ObjectNode)normalizedAfter.path("feasibility")).put("solver_status","same-derived-reason");
  require(normalizedBefore.equals(normalizedAfter),"All other fields exact");
  var output=json.createObjectNode().put("passed",true).put("network_calls",0).put("database_queries_executed",0).put("source_files_modified",0)
   .put("both_stale",true).put("both_unverified",true).put("all_other_day_fields_exact",true).put("stored_plan_not_mutated_by_view",true)
   .put("global_day_end_hashes_even_with_day_override",!rules.fingerprint(input,days.get(1)).equals(rules.fingerprint(changed,days.get(1))));
  output.set("diff",json.valueToTree(differences));System.out.println(json.writeValueAsString(output));
 }
}
