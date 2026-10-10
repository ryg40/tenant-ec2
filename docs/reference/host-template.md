# The host template

[Install](../install.md) | [Architecture](../architecture.md) | [Deploy script](deploy-script.md)

`scripts/aws-host.cfn.yaml` makes the stack of the host.
It includes a VPC with one public subnet, two security groups, an EFS file system and three access points.
It also includes a data volume, a key pair, an instance role under the boundary, an instance and an Elastic IP.
A private bucket is optional.
`EC2_STACK_NAME` in `.env.example` defaults to `tenant-ec2-host`.
The head of the template gives its detailed rules.

## Parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `Project` | `tenant-ec2` | The prefix of the role, instance profile, key pair, secret and log group. |
| `InstanceType` | `t3a.large` | The instance type. Use `m7i-flex.xlarge` when a build needs more memory. |
| `AmiParameter` | The Amazon Linux 2023 x86_64 parameter | The public SSM parameter that gives the image id. |
| `VpcCidr` | None | The private IPv4 range of the VPC. Do not change it after the first deploy. |
| `AdminCidr` | None | The IPv4 range that reaches SSH and selected core edge resources other than `ok`. The prefix length is 16 to 32. |
| `SshPublicKey` | None | The public key of the new key pair of the host. |
| `DataVolumeSize` | `50` | The data volume size in GiB. |
| `SnapshotRepository` | None | The HTTPS clone URL of the portable snapshot, with no credential in it. |
| `SnapshotTag` | None | The portable tag that the host clones. |
| `SnapshotCommit` | None | The commit of the tag, as 40 hexadecimal digits. User data checks it against the clone. |
| `Domain` | None | The domain subtree of the host. |
| `EnablePangolin` | `false` | Opens 51820/udp and 21820/udp only for the Pangolin option. |
| `BedrockEmbedModel` | `amazon.titan-embed-text-v2:0` | The Bedrock embedding model for the gateway. |
| `EnableBucket` | `false` | Makes the private bucket, its policy and the bucket rights of the instance role. |
| `EnableOperations` | `false` | Makes the daily snapshot policy of the data volume, the log group, the billing alarm and its topic. |
| `AlarmEmail` | Empty | The billing alarm address. It is required with `EnableOperations=true`. |
| `AlarmThreshold` | `100` | The billing alarm limit in USD. |

The AMI parameter is `/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64`.
A deploy uses `scripts/aws-host.sh`, profile `tenant-ec2`, region `us-east-1` and capability `CAPABILITY_NAMED_IAM` by default.
The first deploy needs approval.

## Storage and rights

- Keep `EnableBucket=false` and `EnableOperations=false` until the provisioner stack has the reviewed provisioner change. `scripts/aws-provisioner.sh deploy` applies it. See [Operations](#operations).
- The bucket has versions. An old version expires after 30 days; an incomplete upload expires after 7 days.
- CloudFormation makes the bucket name from the stack name. The deploy script writes the output `BucketName` into the environment file.
- The snapshot policy selects the volumes with the tag `Backup=<Project>`. It runs each day at 03:00 UTC and keeps seven snapshots. Its role requires the tag `Project` and the tag `Backup` on a volume.
- The data volume, file system and bucket stay when the stack is deleted. A failed first create deletes them during rollback.
- The file system policy refuses a client without TLS or an access point. It allows mount and write only for the instance role.
- The policy allows no root access. The instance role can use only the three access points of the stack.
- Each access point enforces one POSIX identity. The table below gives the three identities.
- The instance role has no AWS managed policy. Its inline SSM actions do not allow a Parameter Store read.

Warning: retained storage can continue to incur charges after stack deletion.

| EFS access point | Enforced UID:GID and owner of a new root directory | Process |
| --- | --- | --- |
| `open-knowledge` | `10001:10001` | The identity of the pinned OpenKnowledge image. Keep its default user. |
| `openviking` | `1101:1101` | EFS enforces this identity for each file request. The service process does not change. |
| `shared` | `1102:1102` | EFS enforces this identity for each caller. It does not change the identity of a container process. |

- A Git repository of OpenKnowledge on `/data` has the owner of its process. The kit adds no `safe.directory` exception.
- The creation settings of an access point do not change the owner of a file that exists.

Warning: a file system from before this identity needs an ownership migration before use. Plan and approve that migration separately.

## Operations

The operations are optional: the daily snapshots, the logs and the billing alarm.
`EnableOperations` makes their resources. The stack value `EC2_ENABLE_OPERATIONS` controls the [bootstrap](host-boot.md#operations-logs).
`EnableBucket` controls the private bucket separately. Both defaults are `false`.
A change of the template parameter does not run user data again on an instance that exists.
After such an update, set `EC2_ENABLE_OPERATIONS` in the stack values file of the host. Then run the bootstrap again.

`scripts/aws-provisioner.cfn.yaml` holds the rights. The administrator applies it with `scripts/aws-provisioner.sh deploy` before a deploy with a switch at `true`.

| IAM part | Rights and limits |
| --- | --- |
| Host boundary | `s3:ListBucket` on the buckets of the project. `GetObject`, `PutObject`, `DeleteObject` and `AbortMultipartUpload` on their objects. `s3:ResourceAccount` requires this account. No snapshot right. |
| Snapshot role | `bootstrap-<Project>-dlm`. The administrator makes it outside the prefix that the provisioner can change. Its own managed policy is its boundary and its grant. Only DLM can assume it, with a check of the source account and of the policy ARN. |
| Snapshot grant | Describe volumes and snapshots in the allowed regions. Create a snapshot only from a volume of this account with both project tags. A new snapshot needs both tags. Tag only during `CreateSnapshot`. Delete only a snapshot of this account with the project tags and the tag of the DLM policy. The EventBridge rights cover only the rules `AwsDataLifecycleRule.managed-cwe.*`. |
| Provisioner policy | Bucket configuration only on `<Project>-*`, with no object access. A DLM create needs the project request tag. A DLM change needs the project resource tag. `PassRole` names only the snapshot role and `dlm.amazonaws.com`. |
| Logs, alarm and topic | Only the log group `/<Project>/host`. `DescribeLogGroups` is a read on `*` in the allowed regions. The alarm rights name `<Project>-billing`. The SNS rights name `<Project>-alarm`. Both are in `us-east-1`. No SNS publish right. |

- The earlier provisioner grants do not change. The scope of their EC2, EFS and Bedrock rights stays as it is.
- A DLM create and the EC2 describe calls need `Resource: "*"`: AWS has no resource-level limit for them. No added action uses a service wildcard.
- DLM cannot remove the tag `Project`. The topic policy lets only the billing alarm of this account publish.
- The provisioner can delete the bucket, so a failed first create can roll back. The retain policy still keeps a bucket after a successful create.
- The provisioner has no right to upload a template object. So each template stays below 51200 bytes.
- CloudFormation owns the retention of 14 days of the log group.
- The billing metric and its topic use `us-east-1`. A billing alarm is not a spending limit. Its metric can arrive hours after a charge.
- A snapshot is crash-consistent. It is not an application-consistent backup of a database. The kit has no EFS backup.

## First boot

User data writes stack values to `/etc/tenant-ec2/stack.env`.
It clones `SnapshotTag` into `/opt/tenant-ec2` and compares the clone with `SnapshotCommit`.
A different commit stops the run before any script from the clone runs.

The user data then starts `scripts/host-userdata.sh` from the clone.
That script installs the Docker systemd drop-in before it installs Docker.
The drop-in requires the data volume, all three access points and the metadata guard before Docker starts.
The script installs the packages, makes the swap file and mounts the data volume at `/srv/data`.
It mounts each access point at `/srv/efs/<name>`.
It formats the data volume only when both `blkid` and `wipefs -n` find no signature.
It starts the [bootstrap](host-boot.md) when the portable snapshot contains it.

A private portable snapshot needs a read-only clone credential in `EC2_SNAPSHOT_CLONE_TOKEN` of the secret `tenant-ec2/env`.
A Git credential helper reads it through the instance role and supplies it with `GIT_ASKPASS`.
The credential is not in user data, a parameter, a URL or a log line.
Git asks for no credential for a public source.
See [private sources](../options/home-lab.md) for this option.

## Known limits

No live deploy proves the following claims.

- Not verified: the private portable snapshot clone works through the credential helper. The JSON secret needs `EC2_SNAPSHOT_CLONE_TOKEN` before first boot.
- Not verified: an instance restart does not run user data again. A new `SnapshotTag` then needs a replacement instance or a manual run.
- Not verified: an instance replacement can fail when the old instance still holds the data volume attachment.
- Not verified: a stack update reads `AmiParameter` again, so a new Amazon Linux image can replace the instance. The deploy script shows the change set.
- Not verified: EFS accepts the file system policy at create time and the host mounts each access point under it.
- Not verified: the IAM limits on a live account, a daily snapshot, its deletion after seven days and a restore from a snapshot.
- Not verified: the billing alerts preference of the account is on, and the e-mail subscription of the topic is confirmed.
- Not verified: OpenKnowledge on EFS with the identity `10001:10001`. A Git fixture proves the owner alignment, not NFS behavior.
