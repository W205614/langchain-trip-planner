package com.tripplanner.api;

import com.tripplanner.domain.*;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.node.ObjectNode;

@RestController
@RequestMapping("/api/trips")
public class TripsController {
  private final TripService trips;
  private final HistoryController history;
  private final TripLedgerService ledger;
  public TripsController(TripService trips, HistoryController history, TripLedgerService ledger) {
    this.trips = trips; this.history = history; this.ledger = ledger;
  }

  @PostMapping public Object create(HttpServletRequest req, @RequestBody ObjectNode body) {
    return trips.createManual(UsersController.uid(req), body, req.getHeader("X-Request-ID"));
  }
  @GetMapping public Object list(HttpServletRequest req, @RequestParam(defaultValue="1") int page,
      @RequestParam(defaultValue="10") int page_size, @RequestParam(defaultValue="") String city) {
    return history.list(req, page, page_size, city);
  }
  @GetMapping("/{id}") public Object get(HttpServletRequest req, @PathVariable long id) { return history.get(req, id); }
  @PutMapping("/{id}") public Object update(HttpServletRequest req, @PathVariable long id,
      @RequestHeader("If-Match") int version, @RequestBody ObjectNode plan) { return history.update(req, id, version, plan); }
  @DeleteMapping("/{id}") public Object delete(HttpServletRequest req, @PathVariable long id) { return history.delete(req, id); }
  @PostMapping("/{id}/verify") public Object verify(HttpServletRequest req, @PathVariable long id,
      @RequestHeader("If-Match") int version) {
    return trips.reverify(UsersController.uid(req), id, version, req.getHeader("X-Request-ID"));
  }
  @GetMapping("/{id}/versions") public Object versions(HttpServletRequest req, @PathVariable long id,
      @RequestParam(defaultValue="1") int page, @RequestParam(defaultValue="20") int page_size) {
    return ledger.list(UsersController.uid(req), id, page, page_size);
  }
  @GetMapping("/{id}/versions/{version}") public Object version(HttpServletRequest req,
      @PathVariable long id, @PathVariable int version) {
    return ledger.get(UsersController.uid(req), id, version);
  }
  @PostMapping("/{id}/restore") public Object restore(HttpServletRequest req, @PathVariable long id,
      @RequestHeader("If-Match") int version,
      @Valid @RequestBody BusinessTypes.RestoreTripRequest body) {
    return ledger.restore(UsersController.uid(req), id, version, body.source_version(),
        req.getHeader("X-Request-ID"));
  }
}
