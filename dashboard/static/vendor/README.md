# vendor/ — pinned third-party JS

These libraries are **self-hosted** (not loaded from a CDN) so the dashboard has
no third-party runtime dependency and no supply-chain exposure at load time, and
works fully offline.

Each file is the **unmodified, minified upstream build** at the pinned version
below. Do not hand-edit them.

| File | Library | Version | Source (origin of these exact bytes) |
|---|---|---|---|
| `chart.umd.min.js` | Chart.js | 4.4.1 | `https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js` |
| `chartjs-plugin-datalabels.min.js` | chartjs-plugin-datalabels | 2.2.0 | `https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js` |

The SHA-256 of each file is recorded in `SHA256SUMS`.

## Verify the files haven't been tampered with

```bash
cd dashboard/static/vendor && shasum -a 256 -c SHA256SUMS   # expect: each file "OK"
```
