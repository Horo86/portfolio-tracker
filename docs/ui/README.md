# UI

Status: **mockups not yet created.** They are produced before the interface milestone (M5), ideally before the API is finalized (M2), because the screens determine what the API must return (e.g. historical value series, allocations, income by period).

## Principles
Clarity over complexity; visual-first; the dashboard answers "how are my investments performing?" at a glance; avoid jargon (each metric has a plain explanation); gross and net figures side by side with the net highlighted; honest about data quality (stale price, estimated at cost, partial totals); Italian and English; light and dark themes; responsive; WCAG 2.1 AA on main flows.

## Screens to design
1. First-run setup wizard (create the Administrator) and login (with 2FA step)
2. Dashboard: total value (securities + cash), invested capital, gain gross/net (unrealized, realized, income, taxes), history chart with ranges, allocations (asset, type, currency), best/worst, passive income history
3. Accounts list and account view; first-run prompt "Do you already own holdings or cash on this account?"
4. Asset detail: position, average cost, gain gross/net, dividends/coupons received (also for closed positions), price chart, transactions
5. Transaction list (filters, labels such as "does not move cash"), add/edit form per type with computation helpers, bulk edit, delete-all-for-asset
6. "Add holdings I already own": multi-row table
7. Import: choose file → account association → preview (to import / duplicates / not recognized / warnings / new assets) → confirm
8. Asset search/add (ISIN or ticker, choose among matches, confirm type)
9. Manual price entry
10. User settings: profile, password, 2FA activation (QR + text key + code + recovery codes), reporting currency, language, theme
11. Admin: users, market data providers (masked credentials), backup/restore (encryption option, unencrypted warning), security settings
12. Empty, loading, error and partial-data states for each of the above
13. "About" page linking to the source code (AGPL)

## Deliverables
Mockups/wireframes in this folder (any format that can be committed, e.g. HTML or images) plus a short design-tokens note (colors, typography, spacing) shared by light and dark themes. **Mockups must use invented data only.**
