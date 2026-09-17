package com.tripplanner.domain;

import jakarta.validation.constraints.Min;

/** Typed vocabulary for the Java-owned business control plane. */
public final class BusinessTypes {
  private BusinessTypes() {}

  public enum TaskStatus {
    QUEUED("queued"),
    RUNNING("running"),
    SUCCEEDED("succeeded"),
    NEEDS_ATTENTION("needs_attention"),
    FAILED("failed"),
    CANCELLED("cancelled");

    private final String wire;

    TaskStatus(String wire) {
      this.wire = wire;
    }

    public String wire() {
      return wire;
    }

    public boolean terminal() {
      return this == SUCCEEDED || this == NEEDS_ATTENTION || this == FAILED || this == CANCELLED;
    }

    public static TaskStatus fromWire(String value) {
      for (var status : values()) if (status.wire.equals(value)) return status;
      throw new IllegalArgumentException("Unknown task status: " + value);
    }
  }

  public enum TripSource {
    MANUAL("manual"),
    AGENT("agent"),
    ASSISTANT_REVISION("assistant_revision"),
    COPIED("copied");

    private final String wire;

    TripSource(String wire) {
      this.wire = wire;
    }

    public String wire() {
      return wire;
    }
  }

  public enum QualityOutcome {
    COMPLETE("complete"),
    DRAFT("draft"),
    UNASSESSED("unassessed");

    private final String wire;

    QualityOutcome(String wire) {
      this.wire = wire;
    }

    public String wire() {
      return wire;
    }
  }

  public record RestoreTripRequest(@Min(1) int source_version) {}

  public record TripVersionSummary(
      int version, String change_type, String request_id, String title, String source, Object created_at) {}
}
