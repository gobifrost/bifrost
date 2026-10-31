# Hosting Price Inputs — October 31, 2026

These are published USD list prices, not customer quotations or measured cloud
capacity. The benchmark's runtime envelope includes Postgres, Pooler and Redis;
object storage, builds and cluster services are separate costs.

| Premium AMD SKU | Advertised CPU / Memory | Monthly List Price |
|---|---|---:|
| `s-2vcpu-4gb-amd` | 2 vCPU / 4 GiB | $28 |
| `s-4vcpu-8gb-amd` | 4 vCPU / 8 GiB | $56 |
| `s-8vcpu-16gb-amd` | 8 vCPU / 16 GiB | $112 |

The exact SKU rows were extracted from the official page's serialized pricing
data, which includes the Premium AMD option. The default visible Regular table
was not used as an AMD price. Regional availability remains unverified.
[Source: DigitalOcean Droplet Pricing](https://www.digitalocean.com/pricing/droplets).

DigitalOcean lists maximum pod-allocatable memory of approximately 2.5 GiB on a
4 GiB node, 6 GiB on an 8 GiB node, and 13 GiB on a 16 GiB node. Placement must
use observed allocatable resources and other scheduled requests, rather than
advertised RAM. [Source: DOKS Resource Limits](https://docs.digitalocean.com/products/kubernetes/details/limits/).

A read-only production snapshot on October 31 observed the 4-vCPU/8-GiB AMD SKU
with 3890m CPU and `6568236Ki` memory allocatable. Other namespaces on that
single node requested 1323m CPU and about 2.71 GiB memory. This is one current
scheduling snapshot, not a per-node overhead estimate for every future cluster.
The benchmark's permitted maximum of 3900m CPU cannot fit that node even before
other requests. Lower memory alone therefore does not prove higher density.

Raw price HTML, its SHA-256 receipt, and the filtered read-only node-resource
receipt are retained under the private evidence directory's `notes/`, named
`digitalocean-amd-price-receipt-20261031.json` and
`production-node-resources-20261031.json`. They retain neither credentials nor
customer workflow content.

The [demand worksheet](hosting-demand-worksheet.md) additionally states provisioned volumes, retained
objects/backups, node idle and spare capacity, builds, observability, load
balancing and network allocations. Provider/model spend, customer demand,
selling price and cloud performance calibration remain explicit inputs.

## Other Published Billing Inputs

| Item | Published USD Input | Scope |
|---|---:|---|
| Block volume | $0.10 / provisioned GiB-month | The benchmark's 20 GiB volume would be $2/month; used database bytes do not reduce that charge. |
| Spaces Standard subscription | $5/month | Includes 250 GiB across buckets and 1,024 GiB outbound transfer. |
| Additional Spaces Standard storage | $0.02 / GiB-month | Retained current objects and versions need a separate stock estimate. |
| Additional Spaces outbound transfer | $0.01 / GiB | Apply actual network route and included allowance. |
| One regional HTTP load-balancer node | $12/month | A load-balancer node is a billing unit, not a Kubernetes worker. |
| HA control plane | $40/month | Separate from application/database redundancy. |

Sources verified October 31: [Volumes](https://docs.digitalocean.com/products/volumes/details/pricing/),
[Spaces](https://docs.digitalocean.com/products/spaces/details/pricing/),
[Load Balancers](https://docs.digitalocean.com/products/networking/load-balancers/details/pricing/),
and [DOKS Pricing](https://docs.digitalocean.com/products/kubernetes/details/pricing/).

The default control plane without HA has no separate charge. DOKS documentation
states that HA is enabled by default for new clusters created on version 1.36
or later when the API request omits the HA flag. A new-cluster budget must make
that choice explicit. Bundled worker SKUs bill Ready-node time with a 672-hour
monthly cap; v5 workers have no monthly cap. Fixed full-month scenarios avoid
assuming that pod utilization reduces node cost. Variable-size pools require
the provider's billing-span calculation.

For arithmetic only, one $112 AMD node, one 20 GiB block volume, one Spaces
subscription and one regional HTTP load-balancer node total **$131/month**, or
**$171/month** with the HA control plane. This is an infrastructure subtotal,
not a supported tenant placement, complete hosting cost or customer quote.
It excludes backups, additional retained data/egress, builds, observability,
provider/model spend, operating labor and commercial fees. Allocatable CPU on
that larger node and regional availability remain unverified.
