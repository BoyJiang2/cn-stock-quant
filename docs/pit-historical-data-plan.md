# PIT Historical Data Plan

Updated: 2026-07-14

## Verified Local Sources

The installed AkShare version is `1.18.9`. Source capability was checked
against the local package and live responses before assigning follow-up work.

| Data | Source | Historical coverage | Use |
| --- | --- | --- | --- |
| Active listing dates | `stock_info_sh_name_code`, `stock_info_sz_name_code`, `stock_info_bj_name_code` | Current universe with listing dates | PIT listing intervals |
| SH/SZ delistings | `stock_info_sh_delist`, `stock_info_sz_delist` | Historical terminal dates | PIT delisting intervals |
| Shenzhen name changes | `stock_info_sz_change_name` | Dated changes from 1994 onward | Historical ST-prefix proxy and name intervals |
| Current ST | `stock_zh_a_st_em` | Current snapshot only | Forward-only overlay, not historical backtest input |
| CSI members | `index_stock_cons_csindex` | Current snapshot only | Forward snapshots only |
| ChiNext historical adjustments | `index_detail_hist_adjust_cni(symbol="399006")` | 2021-12-13 onward | Historical `399006` membership intervals |

## Completed Baseline

- Listing-date normalization now converts provider strings to `date` before PIT
  persistence.
- 5,280 current listing intervals and 726 SH/SZ delisting intervals were
  synced on 2026-07-14.
- Existing low-confidence snapshot placeholders are removed when a later sync
  obtains a real listing date.
- `000001` resolves as listed from `1991-04-03` for 2024, 2025, and 2026
  PIT queries.

## Next Tasks

- [ ] Add a separate ST-status history axis. Availability and ST are currently
  overloaded in `security_status`; a listed stock must be representable as ST
  at the same time.
- [ ] Build Shenzhen name/ST history from `stock_info_sz_change_name`, using
  change dates as effective dates and low confidence when announcement dates
  are unavailable.
- [ ] Build `399006` membership intervals from
  `index_detail_hist_adjust_cni`; retain `OLD` and additions, exclude removals
  and alternates.
- [ ] Add PIT validation runs that show 2022/2024 universes differ by historical
  ST state and by ChiNext constituent changes.
- [ ] Acquire a genuine historical source for `000300`, `000905`, and `000852`
  before enabling them as PIT-index research universes. Current constituent
  snapshots must not be backdated.

## Data Integrity Rules

- Do not treat current ST names or current CSI constituents as historical data.
- Missing announcement dates must be recorded as degraded confidence.
- Keep the legacy snapshot fallback until a historical source covers the
  requested date; every fallback must surface `pit_degraded=true`.
