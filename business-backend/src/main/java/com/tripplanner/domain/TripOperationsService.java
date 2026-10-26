package com.tripplanner.domain;

import java.util.Map;
import org.springframework.stereotype.Service;
import tools.jackson.databind.node.ObjectNode;

/** Stable controller facade; business behavior belongs to independent modules. */
@Service
public class TripOperationsService {
  private final TripCheckService checks;
  private final TripCollaborationService collaboration;
  private final TripBookingExpenseService booking;
  private final TripNotificationUsageService notifications;
  public TripOperationsService(TripCheckService checks,TripCollaborationService collaboration,
      TripBookingExpenseService booking,TripNotificationUsageService notifications){
    this.checks=checks;this.collaboration=collaboration;this.booking=booking;this.notifications=notifications;
  }
  public Map<String,Object> runCheck(long actor,long id,int expected){return checks.runCheck(actor,id,expected);}
  public Map<String,Object> runCheck(long actor,long id,int expected,Integer onlyDay){return checks.runCheck(actor,id,expected,onlyDay);}
  public Object checks(long actor,long id){return checks.checks(actor,id);}
  public Object checks(long actor,long id,Integer onlyDay){return checks.checks(actor,id,onlyDay);}
  public Object acknowledge(long actor,long id,long riskId,int expected){return checks.acknowledge(actor,id,riskId,expected);}
  public Object workspace(long actor,long id){return collaboration.workspace(actor,id);}
  public Object reorder(long actor,long id,int expected,ObjectNode input,String requestId){return collaboration.reorder(actor,id,expected,input,requestId);}
  public Object invite(long actor,long id,ObjectNode body,String requestId){return collaboration.invite(actor,id,body,requestId);}
  public Object invitations(long actor){return collaboration.invitations(actor);}
  public Object members(long actor,long id){return collaboration.members(actor,id);}
  public Object accept(long actor,long id,String requestId){return collaboration.accept(actor,id,requestId);}
  public Object removeMember(long actor,long id,long member,String requestId){return collaboration.removeMember(actor,id,member,requestId);}
  public Object commitments(long actor,long id){return booking.commitments(actor,id);}
  public Object addCommitment(long actor,long id,ObjectNode body,String requestKey,String requestId){return booking.addCommitment(actor,id,body,requestKey,requestId);}
  public Object updateCommitment(long actor,long id,long commitment,int expected,ObjectNode body,String requestId){return booking.updateCommitment(actor,id,commitment,expected,body,requestId);}
  public Object expenses(long actor,long id){return booking.expenses(actor,id);}
  public Object addExpense(long actor,long id,ObjectNode body,String requestKey,String requestId){return booking.addExpense(actor,id,body,requestKey,requestId);}
  public Object voidExpense(long actor,long id,long expense,ObjectNode body,String requestId){return booking.voidExpense(actor,id,expense,body,requestId);}
  public Object notifications(long actor){return notifications.notifications(actor);}
  public Object readNotification(long actor,long notification){return notifications.readNotification(actor,notification);}
  public Object usage(long actor){return notifications.usage(actor);}
  public Object usagePolicy(long actor,ObjectNode body){return notifications.usagePolicy(actor,body);}
  public Object usagePolicy(long actor,ObjectNode body,String requestId){return notifications.usagePolicy(actor,body,requestId);}
  public void scheduledUsageWarnings(){notifications.scheduledUsageWarnings();}
  public void usageWarnings(){notifications.usageWarnings();}
  public void scheduledDepartureReminders(){notifications.scheduledDepartureReminders();}
  public void departureReminders(){notifications.departureReminders();}
}
