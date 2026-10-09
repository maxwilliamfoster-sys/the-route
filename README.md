# speeds-data

Collected automatically by `.github/workflows/collect-speeds.yml` (on `master`) every 10 minutes.

- `crossings/YYYY-MM-DD.csv`: one row per real stop-to-stop stretch a bus drove. Columns: London date and time, `how` (hour of week, Mon 00:00 = 0), line, from and to stop (ATCO codes), `actual` seconds, `sched` (timetabled) seconds, trip hash.
- `speeds.json`: running totals per `from>to` stop pair per hour of week: `[sum actual s, sum timetabled s, count]`.

Source: Bus Open Data Service GTFS-RT (Open Government Licence), via the THE ROUTE relay.
