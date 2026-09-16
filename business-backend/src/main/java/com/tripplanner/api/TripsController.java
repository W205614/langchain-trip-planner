package com.tripplanner.api;

import com.tripplanner.domain.TripService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.node.ObjectNode;

@RestController
@RequestMapping("/api/trips")
public class TripsController {
  private final TripService trips;
  private final HistoryController history;
  public TripsController(TripService trips, HistoryController history) { this.trips = trips; this.history = history; }

  @PostMapping public Object create(HttpServletRequest req, @RequestBody ObjectNode body) {
    return trips.createManual(UsersController.uid(req), body);
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
      @RequestHeader("If-Match") int version) { return trips.reverify(UsersController.uid(req), id, version); }
}
