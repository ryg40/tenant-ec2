# The DNS records of the host

[Options](README.md) | [Core install](../install.md) | [Deploy script](../reference/deploy-script.md)

The host needs one DNS record for each public name. The setting `EC2_DNS_PROVIDER` says who makes the records. The default is `manual`: you make each record by hand. Three other providers make the records through an API.

## The record set

Each record is an A record to the Elastic IP of the stack. No record is a wildcard.

| Record | When |
| --- | --- |
| `<name>.<EC2_DOMAIN>` for each name of `EC2_EDGE_NAMES` | always |
| `pangolin.<EC2_DOMAIN>` | only with the option `pangolin` in `EC2_OPTIONS` |

The order is `pangolin`, `ok`, `llm`, `ov`, `agent`. The TTL of a new record is 600 seconds.

The core edge gets a certificate for a public name only after the name has its A record. So make the records soon after the deploy.

An example with `EC2_DOMAIN=ec2.example.com`, `EC2_EDGE_NAMES=ok,llm` and the address `203.0.113.10`:

```text
ok.ec2.example.com. 600 IN A 203.0.113.10
llm.ec2.example.com. 600 IN A 203.0.113.10
```

## The settings

Each setting is a name of the environment file `.env` of the development machine. The scripts read these names from the file only, never from the shell environment.

| Name | Values | Default |
| --- | --- | --- |
| `EC2_DOMAIN` | The domain of the host. Each record is one label below it. It has two labels or more. | none |
| `EC2_EDGE_NAMES` | A list of public names with commas and no blank. The known names are `ok`, `llm`, `ov` and `agent`. | `ok` |
| `EC2_OPTIONS` | A list of option names with commas and no blank. The known names are `pangolin` and `copilot`. | empty |
| `EC2_DNS_PROVIDER` | `manual`, `porkbun`, `route53` or `cloudflare`. | `manual` |

A name that a script does not know stops the script. The option `pangolin` needs the provider `porkbun` (section "The option pangolin").

## The commands

| Command | Result |
| --- | --- |
| `scripts/aws-host.sh dns --plan` | It reads the Elastic IP from the stack. It prints the record set. It changes nothing and it calls no provider. |
| `scripts/aws-host.sh dns` | It reads the Elastic IP from the stack. It sets the record set through the provider. |
| `scripts/aws-host.sh --dry-run dns` | It prints each command and makes no call. |
| `scripts/dns-records.sh plan` | It prints the record set with no stack. Give the address with `--address <address>`, or the command shows a stand-in. |
| `scripts/dns-records.sh --address <address> apply` | It sets the record set with an address that you give. |

`apply` reads each record first. It changes only a record that differs. It makes a record that is not there. It corrects a record with a different address. A record that is correct gets no change, so a second run changes nothing. `apply` deletes no record.

The result of `apply` is one line for each record:

```text
dns: ok.ec2.example.com A 203.0.113.10: no change
dns: llm.ec2.example.com A 203.0.113.10: done (create)
```

## Where a DNS credential is

A credential of a DNS provider stays on the development machine. It is in `.env`, with the mode 0600. It does not go to the host.

- `scripts/aws-host.sh secret` leaves each name of a DNS provider block of `.env.example` out of the Secrets Manager secret. It prints each name that it leaves out.
- The core edge gets one certificate for each name with the HTTP-01 or the TLS-ALPN-01 challenge. So the host needs no DNS key.
- A script sends a credential in a file with the mode 0600 that the tool reads. No argument of a command, no address and no line of the output holds a credential.

The reason: the host is public. A DNS key on the host lets an attacker of the host change the records of the zone. Then the attacker points a name to another server and gets a valid certificate for it.

The one exception is the Porkbun pair with the option `pangolin` (section "The option pangolin").

## The provider manual

| Item | Value |
| --- | --- |
| Names | none |
| Credential | none |

1. Run `scripts/aws-host.sh dns`. It prints each record in the form of a zone file.
2. Make each record at the DNS provider of your domain. The type is A and the value is the address of the line.
3. Wait until the records answer. Run `dig +short ok.<EC2_DOMAIN>`; the result must be the Elastic IP.

The manual provider makes no DNS call. `scripts/aws-host.sh dns` still reads the stack through AWS.
For a plan with no AWS call, use `scripts/dns-records.sh plan`.
A second run prints the same lines.

## The provider porkbun

| Item | Value |
| --- | --- |
| Names | `EC2_PORKBUN_API_KEY`, `EC2_PORKBUN_SECRET_KEY` |
| Tool | `curl` |
| Smallest scope | One key pair of the Porkbun account, with API access on for the one domain only. The pair changes each record of that domain: Porkbun gives no smaller scope. |

`EC2_DOMAIN` must have the form `<label>.<domain>`: one label, then the domain of the Porkbun account. An example is `ec2.example.com` for the Porkbun domain `example.com`.

1. In the Porkbun account, turn API access on for the domain of the host only. Keep it off for each other domain.
2. Make one API key pair for this host. Do not use a pair of another system.
3. Set `EC2_DNS_PROVIDER=porkbun` and the two names in `.env`.
4. Run `scripts/aws-host.sh dns --plan`. Read the record set.
5. Run `scripts/aws-host.sh dns`.

A record is correct when each A record of its name holds the address.

## The provider route53

| Item | Value |
| --- | --- |
| Names | `EC2_ROUTE53_PROFILE`, `EC2_ROUTE53_HOSTED_ZONE_ID` |
| Tool | the AWS CLI version 2 |
| Smallest scope | One IAM identity with the two statements below: a read of the record sets of one hosted zone, and the action `UPSERT` on the A records of the host only. |

`EC2_ROUTE53_PROFILE` is an AWS CLI profile of your own. It is not the provisioner profile: the provisioner has no Route 53 right, and this kit does not give it one. The environment file holds the name of the profile only. The AWS CLI holds the credential.

The IAM statements that the profile needs. Write the id of your hosted zone and one line for each record of your record set:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadTheRecordSetsOfTheZone",
      "Effect": "Allow",
      "Action": "route53:ListResourceRecordSets",
      "Resource": "arn:aws:route53:::hostedzone/<hosted zone id>"
    },
    {
      "Sid": "SetTheRecordsOfTheHost",
      "Effect": "Allow",
      "Action": "route53:ChangeResourceRecordSets",
      "Resource": "arn:aws:route53:::hostedzone/<hosted zone id>",
      "Condition": {
        "ForAllValues:StringEquals": {
          "route53:ChangeResourceRecordSetsNormalizedRecordNames": [
            "ok.ec2.example.com"
          ],
          "route53:ChangeResourceRecordSetsRecordTypes": ["A"],
          "route53:ChangeResourceRecordSetsActions": ["UPSERT"]
        }
      }
    }
  ]
}
```

Write each record name in lower case and with no dot at its end. The three condition keys apply to `route53:ChangeResourceRecordSets` only, so the read has its own statement.

1. Find the id of the hosted zone that holds `EC2_DOMAIN`: `aws route53 list-hosted-zones` with an identity that can list the zones, or the Route 53 console.
2. Make an IAM policy with the two statements. Attach it to a user or a role of your own.
3. Make an AWS CLI profile for that identity.
4. Set `EC2_DNS_PROVIDER=route53` and the two names in `.env`.
5. Run `scripts/aws-host.sh dns --plan`. Read the record set.
6. Run `scripts/aws-host.sh dns`.

A record is correct when its name has one A record set with the address as its one value, with no alias and no routing policy. The script does not wait for the end of a change: a wait needs the right `route53:GetChange`. A new record answers after about one minute.

## The provider cloudflare

| Item | Value |
| --- | --- |
| Names | `EC2_CLOUDFLARE_API_TOKEN`, `EC2_CLOUDFLARE_ZONE_ID` |
| Tool | `curl` 7.55 or later |
| Smallest scope | One API token with the permission to edit the DNS records of one zone (Zone, DNS, Edit), and that zone as its only zone resource. The token changes each record of that zone: Cloudflare gives no smaller scope. |

`EC2_CLOUDFLARE_ZONE_ID` is the id of the zone: 32 hexadecimal digits. It is not a secret. With the id in the file, the token needs no right to list the zones.

1. In the Cloudflare dashboard, make an API token from the template "Edit zone DNS".
2. Under "Zone Resources", select the one zone that holds `EC2_DOMAIN`. Select no other zone.
3. Copy the zone id from the overview page of the zone.
4. Set `EC2_DNS_PROVIDER=cloudflare` and the two names in `.env`.
5. Run `scripts/aws-host.sh dns --plan`. Read the record set.
6. Run `scripts/aws-host.sh dns`.

A record is correct when its name has one A record with the address, and the record is not proxied ("DNS only"). The script makes and corrects each record as not proxied. A proxied record gives an address of Cloudflare, not the address of the host. The certificate of the edge and a tunnel need the address of the host.

A name with more than one A record stops the script. Delete each record but one by hand, then run the command again.

## The option pangolin

The Pangolin edge makes one wildcard certificate with the DNS-01 challenge through Porkbun. So the option `pangolin` needs `EC2_DNS_PROVIDER=porkbun`, and each script stops with another provider. The page [Pangolin edge](pangolin.md) gives the steps of the option.

With this option, the Porkbun pair goes into the Secrets Manager secret: the edge needs it on the host. `scripts/aws-host.sh secret` then needs a value for the two names.

Warning: with the option `pangolin`, a DNS key for the whole Porkbun domain is on the public host. Keep API access off for each other domain of the account.

## Not verified

No real call to a DNS provider ran. Each test used a stub of the tool with a fake zone.

- Not verified: the answers of the Porkbun API, of Route 53 and of the Cloudflare API have the form that the scripts read.
- Not verified: an identity with only the two IAM statements above passes the two Route 53 calls.
- Not verified: the AWS CLI needs no region for a Route 53 call.
- Not verified: a Cloudflare token with only the permission Zone, DNS, Edit on one zone passes the three requests.
- Not verified: the names of the pages and of the fields in the Porkbun account and in the Cloudflare dashboard.
