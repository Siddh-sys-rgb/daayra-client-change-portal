# Working browser verification

The application was exercised locally on macOS with Python 3.12, through the actual browser UI and fictional demo data. These screenshots show the working product, not generated mockups.

- Signed in as the fictional designer and reviewed the client-submitted version.
- Accepted version 1: agreed estimate changed from INR28000/14days to INR32500/17days.
- Reopened the decision trail and restarted the server: the accepted version and project plan persisted.

All six apps were checked at a 390×844 viewport; this app's document width was 390px with no horizontal overflow. The temporary viewport was reset after the check. The fresh final app load reported no JavaScript errors. Desktop and mobile captures can show different points in the walkthrough.

Automated regression suite: **89 passing tests**. The README describes test scope and measured coverage. These checks do not establish production scale or complete security coverage.
