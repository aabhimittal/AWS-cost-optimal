# Database & Networking

Two areas where costs hide in plain sight: databases (over-provisioned and always-on)
and data transfer (invisible until the bill arrives).

## Databases (RDS / Aurora / DynamoDB)

### Right-size and schedule
- DB instances are often sized for peak and run 24/7 at low utilization — classic
  flat-region waste. Use Performance Insights to find real headroom.
- **Stop non-prod databases nights/weekends.** RDS can be stopped (up to 7 days at a
  time) or automated to zero. Dev/test DBs rarely need to run 168 hrs/week.

### Aurora Serverless v2 — the scaling tradeoff
Scales capacity (ACUs) with load. Wins for spiky/intermittent workloads; a steady
high-load DB is cheaper on provisioned Aurora. Same breakeven logic as serverless compute.

### Read replicas vs. caching
Adding read replicas scales reads but multiplies instance cost. Often a **cache**
(ElastiCache/DAX) in front is cheaper *and* faster for hot reads — trading a little
consistency lag for large cost + latency wins. Reach for cache before replica #3.

### DynamoDB capacity mode
| Mode | Best for |
|------|----------|
| On-demand | Unknown/spiky traffic, dev — pay per request |
| Provisioned + auto-scaling | Predictable traffic — much cheaper at steady volume |

Switching a steady-traffic table from on-demand to provisioned is a common 50–70% cut.

### Commitment discounts
Reserved Instances for RDS/ElastiCache and Reserved Capacity for DynamoDB apply the
same "cover the baseline" logic as compute Savings Plans.

## Networking — the invisible bill

Data transfer is metered, asymmetric, and easy to ignore until it dominates.

### The expensive directions
- **Internet egress** (out to users) is the priciest — cache at the edge with
  **CloudFront**; served-from-cache bytes skip repeated origin egress and are cheaper.
- **Cross-AZ traffic** is charged in *both* directions. Chatty microservices spread
  across AZs can rack up transfer costs invisibly. Keep hot paths AZ-local where
  the reliability tradeoff allows.
- **Cross-region** is pricier still — replicate deliberately, not by default.

### The NAT Gateway trap
NAT Gateways charge per-hour *and* per-GB processed. A private subnet pulling large
volumes (container images, package installs, S3 reads) through NAT can cost more in
processing than the compute it serves.

- Use **VPC Gateway Endpoints for S3 and DynamoDB** — free, and they bypass NAT
  entirely. This alone often pays for the migration effort in weeks.
- Use Interface Endpoints (PrivateLink) for other AWS services to cut NAT + egress.
- Pull base images from ECR in-region, not Docker Hub through NAT.

### Right-size the edge
Match CloudFront price class to your actual user geography — you may not need all
edge locations. And check that VPC endpoints, not NAT, carry your AWS-service traffic.

→ Next: [Novel approaches](novel-approaches.md)
