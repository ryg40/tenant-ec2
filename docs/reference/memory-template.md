# The memory template

`scripts/aws-memory-<stage>.cfn.yaml` defines one shared memory stack for `dev`, `test` or `prod`.
The stack name is `bootstrap-<Project>-memory-<Stage>`.
It is separate from the host stack and the provisioner stack.
The administrator deploys it. The provisioner and homelab host role get no new rights.
Optional `hostRoles` admission supports the separate [Mac role](host-template-macos.md), not the homelab role.
See the [host admission contract](../memory-stack.md#optional-mac-host-admission) for scope and the pending agent release.

The source is `config/memory/<stage>.yaml`.
Run `python3 scripts/aws-memory.py render --stage dev` after a change to that file.
Run `python3 scripts/aws-memory.py check --stage dev` to compare the generated template with its source.
The deploy command runs this check before any AWS call.
Never edit a generated template by hand.

## Parameters and settings

| Parameter | Default | Meaning |
| --- | --- | --- |
| `Project` | `tenant-ec2` | The prefix of the memory, bucket, users and boundary. |
| `Stage` | The generated stage | One of `dev`, `test` or `prod`. Use the template of that stage. |
| `AdministratorRole` | `Administrator` | One IAM role name allowed by the bucket rule, without a path or wildcard. |
| `AdministratorUser` | Empty | An optional console administrator IAM user name, without a path or wildcard. Require MFA separately. |

The region is the deploy region, not a CloudFormation parameter.
Keep `EC2_AWS_REGION` equal to `region` in the stage file.
A change of region selects another stack, not an update of the first stack.
CloudFormation uses `AWS::AccountId` and `AWS::Partition`; no tracked file supplies an account number.
See [AWS pseudo parameters](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/pseudo-parameter-reference.html), sections `AWS::AccountId` and `AWS::Partition`.

| Shell setting | Default | Meaning |
| --- | --- | --- |
| `EC2_PROJECT` | `tenant-ec2` | The project prefix; the script accepts lowercase letters, digits and hyphens, starting with a letter. |
| `EC2_AWS_REGION` | `us-east-1` | The region for every AWS CLI call. |
| `EC2_AWS_ADMIN_PROFILE` | `tenant-ec2-admin` | The profile for read calls and printed plans, unless `--profile` overrides it. |
| `EC2_MEMORY_ADMINISTRATOR_ROLE` | `Administrator` | The value passed to `AdministratorRole` during deploy. |
| `EC2_MEMORY_ADMINISTRATOR_USER` | Empty | The value passed to `AdministratorUser` during deploy, including empty to remove a previous exception. |

Every AWS write requires both `--apply` and an explicit `--profile`.
Without `--apply`, deploy prints its command and makes no AWS call.
Keys, rotate and delete-stack use read calls to prepare their plans, but make no AWS write without `--apply`.
`--dry-run` makes this intent explicit. It cannot be combined with `--apply`.
The script disables AWS paging and interactive prompts.

Warning: `--apply` can create billed resources, change access, or delete IAM resources.

## Resources

| Resource | Purpose and limits |
| --- | --- |
| `Memory` | `AWS::BedrockAgentCore::Memory`, named with underscores in place of project hyphens, then `_memory_<stage>`. Event expiry comes from the stage file. No strategy, execution role or custom encryption key is configured. |
| `Bucket` | Optional S3 bucket `<Project>-memory-<Stage>`. It blocks public access, uses SSE-S3 encryption, and keeps object versions. |
| `BucketPolicy` | Requires TLS. `DenyOutsiders` denies access except for stack users, account root, `AdministratorRole` and optional `AdministratorUser`. |
| `Boundary` | A managed policy that limits every stack user to this memory and bucket. It allows `sts:GetCallerIdentity` too. |
| Person users | One IAM user `<Project>-memory-<Stage>-person-<actorId>` per active person, with the inline policy `agentcore-memory`. |
| Service users | Optional `<Project>-memory-<Stage>-submit` and `<Project>-memory-<Stage>-worker`, each with a customer managed policy. |

The memory and bucket use `RetainExceptOnCreate` for deletion and `Retain` for replacement.
A failed first create can remove them during rollback. A later stack deletion keeps them.
The bucket expires noncurrent versions after 30 days and aborts incomplete multipart uploads after 7 days.
These rules follow the host bucket design.
See [DeletionPolicy](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-attribute-deletionpolicy.html), section `RetainExceptOnCreate`, and [UpdateReplacePolicy](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-attribute-updatereplacepolicy.html), section `Retain`.
See [S3 lifecycle rules](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-s3-bucket-rule.html), properties `NoncurrentVersionExpiration` and `AbortIncompleteMultipartUpload`.

A permissions boundary is the maximum permission set of a user. It does not grant access by itself.
The inline or attached policy grants access within that maximum.
See [Permissions boundaries](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_boundaries.html), section `Evaluating effective permissions with boundaries`.

The bucket follows the OpenKnowledge file structure, with only `projects/` and `users/` as top-level prefixes.
Memory `/actor/<a>/` goes with `users/<a>/`; memory `/project/<p>/...` goes with `projects/<p>/`.
For example, `/project/example-project/shared/` maps to files under `projects/example-project/`.
The mapping does not synchronize records and files.

| Principal | Bucket access |
| --- | --- |
| Person | Read and write each member project `projects/<p>/*`; read, write and delete own `users/<a>/*`. |
| Maintainer | The same file access as a member. Memory review permissions stay separate. |
| Deleter | Also delete `projects/<p>/*` for each project that lists the person as a deleter. |
| Submit user | Read and write `projects/*` and `users/*`; no delete. |
| Worker user | Read `projects/*`; no access to `users/*`. |
| Administrator | All, subject to the bucket exception and TLS rule. |

`s3:ListBucket` uses `s3:prefix` conditions with those same readable prefixes.
The boundary limits resources; each user policy limits namespaces and prefixes.
The bucket policy is a resource policy: a rule attached to the bucket itself.
Its deny rule uses `aws:PrincipalArn` to exclude identities outside the allowed set.
See [Global condition keys](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_condition-keys.html), section `aws:PrincipalArn`.

`HasAdministratorUser` tests whether `AdministratorUser` is not empty.
When set, the exception includes `arn:${AWS::Partition}:iam::${AWS::AccountId}:user/${AdministratorUser}`.
When empty, `Fn::If` returns `AWS::NoValue` and omits the user ARN from the list.
The exception removes only this deny; the administrator still needs an identity policy that grants the requested action.
Use an MFA administrator session, not root. Enforce MFA separately; the exception does not enforce it.
The role and root exceptions remain unchanged.

The dev fixture keeps Bruno in `example-site` only because both memberships give a resolved policy bound of 2,250 characters.
IAM permits 2,048 characters for all inline policies of a user; the largest current example bound is 2,040.
The renderer refuses a larger policy without changing the memory statements or their order.

Not verified: a resource policy on a memory.
The template renderer and the design document record whether AWS supports that extra rule.
Do not infer a memory resource policy from the bucket policy.
See [AgentCore ResourcePolicy](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-resourcepolicy.html), property `ResourceArn`.

No template creates an access key or a console password.
IAM returns a secret access key only during creation. A later read cannot recover it.
See [CreateAccessKey](https://docs.aws.amazon.com/IAM/latest/APIReference/API_CreateAccessKey.html), section `Response Elements`.

## Outputs

| Output | Value |
| --- | --- |
| `MemoryId` | The private memory identifier used by the client config. |
| `MemoryArn` | The Amazon Resource Name of this memory. |
| `BucketName` | The bucket name, when the bucket is enabled. |
| `BoundaryArn` | The Amazon Resource Name of the users' permissions boundary. |
| `PersonUsers` | A comma-separated list of active person user names. An empty list has an empty value. |
| `SubmitUser` | The submit user name, when enabled; absent or empty when disabled. |
| `WorkerUser` | The worker user name, when enabled; absent or empty when disabled. |

`outputs` prints a JSON object with these field names. It replaces account numbers with `<account>` in the printed text.
`outputs --write` saves the unredacted object in `.local/memory/<stage>.json` with mode `0600`.
It replaces the file atomically. It refuses a symbolic link at the file or either local directory.
`.local/` is already ignored by Git. The cache holds no access key.
`client-config` reads `MemoryId` from this object, not from a live AWS call.

Warning: outputs and client-config contain a private memory identifier. Keep their output out of commits and public logs.

## Commands

Run commands from the repository root.
A dry-run command is a plan, not approval to deploy.
The examples use `dev`; choose the stage explicitly for each command.

1. Preview a deploy.
   The renderer must exist, and its check must pass before the script prints the AWS command.

   ```sh
   scripts/aws-memory.sh deploy --stage dev --dry-run
   ```

For an IAM user administrator, set `EC2_MEMORY_ADMINISTRATOR_USER` to the reviewed user name before the preview and apply.
Keep this setting on later updates to retain the user exception; an unset setting passes an empty value and removes it.
Keep the user's actual name in the shell, not in tracked files.

2. Apply an approved deploy.
   The CLI creates a change set with `--no-execute-changeset`, then the script prints its resource changes before execution.

   ```sh
   scripts/aws-memory.sh deploy --stage dev --apply --profile tenant-ec2-admin
   ```

The script uses the exact change set returned by the CLI, never the most recent change set found in a list.
It refuses an unreadable or unavailable change set. An empty change set makes no execution call.
It starts execution without waiting for the stack to finish. Check stack status before continuing.
See [CloudFormation deploy](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/deploy.html), option `--no-execute-changeset`.

3. Read and save the outputs.
   The saved file supplies the memory identifier for client-config.

   ```sh
   scripts/aws-memory.sh outputs --stage dev --write --profile tenant-ec2-admin
   ```

4. List the users.
   The script reads only the user names from this stack's outputs, never every IAM user in the account.

   ```sh
   scripts/aws-memory.sh users --stage dev --profile tenant-ec2-admin
   ```

5. Preview users that need a key.
   Users with one key stay unchanged; two keys require completion of a rotation first.

   ```sh
   scripts/aws-memory.sh keys --stage dev --profile tenant-ec2-admin
   ```

6. Create and save missing keys after approval.
   The script checks for `bw-session-update.sh` before creation and passes each value on stdin, never as an argument.

   ```sh
   scripts/aws-memory.sh keys --stage dev --apply --profile tenant-ec2-admin
   ```

The script prints user names only during key creation.
The sync names are `TENANT_EC2_MEMORY_<STAGE>_<USER>_KEY_ID` and `TENANT_EC2_MEMORY_<STAGE>_<USER>_SECRET`.
`<USER>` is `PERSON_<ACTORID>`, `SUBMIT` or `WORKER`, in capitals with hyphens replaced by underscores.
The sync command owns persistence in Bitwarden and its managed shell block. This script writes no key file.
An unavailable sync command stops creation. A sync failure attempts to delete only the new key.
It never deletes an older key automatically. A failed rollback requires manual IAM inspection.
A failed sync can leave a partial saved pair; repair the saved values before using them.

7. Rotate one stack user's key after approval.
   Rotation requires exactly one existing key, creates the second, and prints the delete command for the first.

   ```sh
   scripts/aws-memory.sh rotate --stage dev --user tenant-ec2-memory-dev-person-alice --apply --profile tenant-ec2-admin
   ```

Do not run the printed delete command until the new key works for the recipient.
A dry rotation prints the user name and creates nothing.
See [Managing access keys](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html), section `Rotating access keys`.

8. Check key count and policy attachment.
   Each user gets one line with `keys`, `boundary` and `policy`; a difference returns exit code 1.

   ```sh
   scripts/aws-memory.sh check --stage dev --profile tenant-ec2-admin
   ```

This check compares the attached boundary with `BoundaryArn`.
It checks for the person inline policy or a customer managed service policy.
It does not compare policy contents, simulate namespace access, or verify that a key is active.

9. Render a person's client config.
   This calls `aws-memory.py client-config` with `--stage`, `--actor` and the saved `--memory-id`.

   ```sh
   scripts/aws-memory.sh client-config --stage dev --actor alice
   ```

Hand the key and client config to the person through a password-protected Bitwarden Send.
Set one access and one day. Give the password through a separate channel.
The recipient stores the key under the profile named by the client config, with file mode `0600`.
The client config holds no key. Never put a key in this repository to prepare the Send.
See [Send privacy](https://bitwarden.com/help/send-privacy/) and [Send lifespan](https://bitwarden.com/help/send-lifespan/).

10. Preview a stack deletion.
    The script prints the retained memory and bucket before the delete command.

    ```sh
    scripts/aws-memory.sh delete-stack --stage dev --profile tenant-ec2-admin
    ```

11. Delete a production stack only after separate approval.
    Production requires `--i-know` as well as both write flags.

    ```sh
    scripts/aws-memory.sh delete-stack --stage prod --i-know --apply --profile tenant-ec2-admin
    ```

Delete each user's access keys separately before stack deletion or offboarding through CloudFormation.
Keys created outside CloudFormation can prevent deletion of an IAM user.
The script does not delete records, bucket objects or earlier hand-made memory users.
See [Deleting an IAM user](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_users_manage.html#id_users_deleting).

Warning: retained memory and bucket resources can continue to incur charges after stack deletion.

## Verification limits

Not verified: a live deploy, update, key creation, sync, rotation, offboarding or stack deletion.
Not verified: end-to-end use of the rendered client config with tenant-pi's stage-specific user prefix.
Not verified: a complete Bitwarden Send handover.
The offline script tests use isolated AWS and credential-sync stubs. They make no AWS call and create no real key.
