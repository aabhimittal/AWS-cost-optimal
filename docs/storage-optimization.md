# Storage Optimization

Storage cost is dominated by **what tier data sits in vs. how often you actually touch it**.
The tradeoff axis is retrieval latency and per-request cost, not throughput.

## S3 — tier by access pattern

| Tier | Best for | The tradeoff |
|------|----------|--------------|
| Standard | Hot, frequent access | Highest storage $, no retrieval fee |
| Intelligent-Tiering | Unknown/changing patterns | Auto-moves tiers; small monitoring fee |
| Standard-IA / One Zone-IA | Warm, infrequent | Cheaper storage, **per-GB retrieval fee** |
| Glacier Instant | Archive, rare but instant | Cheap storage, higher retrieval cost |
| Glacier Flexible / Deep Archive | Cold compliance data | Cheapest storage, **minutes–hours to restore** |

**Default to Intelligent-Tiering** when access is unpredictable — it captures most of
the savings with zero risk of a wrong manual tier. Use lifecycle rules only when you
*know* the pattern (logs → IA at 30d → Glacier at 90d → expire at 365d).

### The retrieval-latency tax
Colder tiers are cheaper *to store* but charge *to read* — in both dollars and time.
Archiving data you'll query next quarter can cost more (retrieval fees) than keeping
it warm. Tier by access pattern, not by age alone.

### Free wins
- Delete incomplete multipart uploads (lifecycle rule) — silent, invisible waste.
- Expire old versions if versioning is on but you don't need deep history.
- Turn on S3 Storage Lens to *see* the flat-region waste before cutting.

## EBS — match volume type to the IO curve

| Type | When | Tradeoff |
|------|------|----------|
| gp3 | Default for most | Decouples IOPS/throughput from size — usually cheaper than gp2 |
| io2 Block Express | Latency-critical DBs | Premium price for guaranteed IOPS |
| st1/sc1 (HDD) | Big sequential (logs, data lake staging) | Cheap $/GB, poor random IO |

- **Migrate gp2 → gp3.** Almost always cheaper at equal-or-better performance —
  a top-left, near-zero-risk move.
- **Kill unattached volumes and old snapshots.** Detached EBS bills at full rate
  forever. Snapshots compound. Both are pure flat-region waste.
- Right-size provisioned IOPS to measured need; provisioned-but-unused IOPS is a
  common hidden cost.

## The snapshot/backup trap

Backups feel free to create and quietly accumulate. Set a **retention policy as code**
(AWS Backup / DLM) with tiering to cold storage for compliance copies. Unbounded
snapshot retention is one of the most common six-figure surprises at scale.

→ Next: [Database & networking](database-and-networking.md)
