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
The preview and the prepare step of deploy run this check before any AWS call.
The execute step does not run it: the change set already holds its template.
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
| `EC2_MEMORY_ADMINISTRATOR_ROLE` | `Administrator` | The value passed to `AdministratorRole` during deploy. The execute step compares it with the change set. |
| `EC2_MEMORY_ADMINISTRATOR_USER` | Empty | The value passed to `AdministratorUser` during deploy, including empty to remove a previous exception. The execute step compares it with the change set. |

Every AWS write requires a write flag and an explicit `--profile`.
The write flags of deploy are `--prepare` and `--execute <change-set-name>`.
The write flag of keys, rotate and delete-stack is `--apply`.
Deploy refuses `--apply` and names the two steps.
Without a write flag, deploy prints its command and makes no AWS call.
Keys, rotate and delete-stack use read calls to prepare their plans, but make no AWS write without `--apply`.
`--dry-run` makes this intent explicit. It cannot be combined with a write flag.
The script disables AWS paging and interactive prompts.

Warning: a write flag can create billed resources, change access, or delete IAM resources.

## Resources

| Resource | Purpose and limits |
| --- | --- |
| `Memory` | `AWS::BedrockAgentCore::Memory`, named with underscores in place of project hyphens, then `_memory_<stage>`. Event expiry comes from the stage file. No strategy, execution role or custom encryption key is configured. |
| `Bucket` | Optional S3 bucket `<Project>-memory-<Stage>`. It blocks public access, uses SSE-S3 encryption, and keeps object versions. |
| `BucketPolicy` | Requires TLS. `DenyOutsiders` denies access except for stack users, account root, `AdministratorRole`, optional `AdministratorUser` and an admitted host role. |
| `Boundary` | A managed policy that limits every stack user to this memory and bucket. It allows `sts:GetCallerIdentity` too. |
| Person users | One IAM user `<Project>-memory-<Stage>-person-<actorId>` per active person, with the inline policy `agentcore-memory` and the tags `purpose`, `actorId`, `pin` and `Stage`. |
| Service users | Optional `<Project>-memory-<Stage>-submit` and `<Project>-memory-<Stage>-worker`, each with a customer managed policy and the tags `Project`, `Stage` and `purpose`. |

The deploy helper gives the stack the tags `Project` and `Stage`.
CloudFormation copies each stack tag to each resource that takes tags.
IAM treats two tag keys that differ only in case as one key, so such a pair fails the create of an IAM user.
Thus each resource tag key keeps the case of the stack tag key: a person user carries `Stage`, not `stage`.
Keep this rule when you add a tag to the renderer. The render check cannot find a case conflict, because it exists only in IAM at create time.

The memory, the bucket and the bucket policy use `RetainExceptOnCreate` for deletion and `Retain` for replacement.
A failed first create removes them during rollback. A later stack deletion keeps them.
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

A deploy has two steps with a pause between them.
A change set is the list of resource changes that CloudFormation proposes for a stack.
The first step creates the change set and shows it. The second step executes that change set.
An owner word is the explicit approval of the deployment owner for one step.
Each step needs its own owner word. The owner word for the first step does not approve the second step.

1. Preview a deploy.
   The renderer must exist, and its check must pass before the script prints the AWS command.
   This step needs no owner word. It makes no AWS call.

   ```sh
   scripts/aws-memory.sh deploy --stage dev --dry-run
   ```

For an IAM user administrator, set `EC2_MEMORY_ADMINISTRATOR_USER` to the reviewed user name before the preview and both deploy steps.
Keep this setting on later updates to retain the user exception; an unset setting passes an empty value and removes it.
Keep the user's actual name in the shell, not in tracked files.

2. Prepare the change set.
   Owner word for this step: the owner approves the creation of a change set for this stage, with this profile.
   The CLI creates the change set with `--no-execute-changeset` and waits for its creation.
   The script prints the stack, the change set name, its status, its execution status and its resource changes.
   Then it stops and prints the execute command for step 3.

   ```sh
   scripts/aws-memory.sh deploy --stage dev --prepare --profile tenant-ec2-admin
   ```

The prepare step changes no stack resource.
For a new stack, CloudFormation creates the stack entry with the status `REVIEW_IN_PROGRESS`, with no template and no resources.
The script uses the exact change set returned by the CLI, never the most recent change set found in a list.
It refuses a change set that is not ready. An empty change set prints `No changes to deploy.` and leaves nothing to execute.
See [CloudFormation deploy](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/deploy.html), option `--no-execute-changeset`.
See [CreateChangeSet](https://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_CreateChangeSet.html), parameter `ChangeSetType`.

3. Execute the reviewed change set.
   Owner word for this step: the owner reads the resource changes of step 2 and approves the execution of that change set by its name.
   Give the change set name that step 2 printed. The script accepts a name only, not an ARN.

   ```sh
   scripts/aws-memory.sh deploy --stage dev --execute <change-set-name> --profile tenant-ec2-admin
   ```

The script reads the named change set of this stage's stack and prints its status and resource changes again.
It executes the change set only when each of these is true:

- The change set belongs to the stack `bootstrap-<Project>-memory-<Stage>` in the deploy region and has the given name.
- Its status is `CREATE_COMPLETE` and its execution status is `AVAILABLE`.
- Its parameters `Project`, `Stage`, `AdministratorRole` and `AdministratorUser` equal the current settings, and it has no other parameter.

It executes the exact change set that it checked. It never selects another change set or the latest one.
Keep `EC2_PROJECT`, `EC2_AWS_REGION`, `EC2_MEMORY_ADMINISTRATOR_ROLE` and `EC2_MEMORY_ADMINISTRATOR_USER` the same in both steps.
A different administrator setting in step 3 refuses the execution; run step 2 again with the reviewed settings.
The execute step does not run the renderer: the change set already holds its template.
It starts execution without waiting for the stack to finish. It prints a read-only status command; run it before continuing.
A change set that was executed, replaced by another update or failed has another status; the script refuses it.
See [ExecuteChangeSet](https://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_ExecuteChangeSet.html), section `Request Parameters`.

A change set that the owner does not approve stays unexecuted.
Delete it with `aws cloudformation delete-change-set` and the same stack and change set name.
A new stack that stays in `REVIEW_IN_PROGRESS` holds no resources; `delete-stack` of this script cannot remove it, because it reads stack outputs first.
Not verified: the clean-up of an unapproved change set in a live account.

An exit code 0 of the execute step means that the execution started, not that the stack is complete.
A failed first create rolls back and leaves the stack in `ROLLBACK_COMPLETE`, with no resources.
A new prepare step for the same stack name needs the deletion of that stack first.
`delete-stack` of this script cannot remove it, because it reads stack outputs first.
After the owner word, delete it with `aws cloudformation delete-stack` and the stack name, with the administrator profile.

4. Read and save the outputs.
   The saved file supplies the memory identifier for client-config.

   ```sh
   scripts/aws-memory.sh outputs --stage dev --write --profile tenant-ec2-admin
   ```

5. List the users.
   The script reads only the user names from this stack's outputs, never every IAM user in the account.

   ```sh
   scripts/aws-memory.sh users --stage dev --profile tenant-ec2-admin
   ```

6. Preview users that need a key.
   Users with one key stay unchanged; two keys require completion of a rotation first.

   ```sh
   scripts/aws-memory.sh keys --stage dev --profile tenant-ec2-admin
   ```

7. Create and save missing keys after approval.
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

8. Rotate one stack user's key after approval.
   Rotation requires exactly one existing key, creates the second, and prints the delete command for the first.

   ```sh
   scripts/aws-memory.sh rotate --stage dev --user tenant-ec2-memory-dev-person-alice --apply --profile tenant-ec2-admin
   ```

Do not run the printed delete command until the new key works for the recipient.
A dry rotation prints the user name and creates nothing.
See [Managing access keys](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html), section `Rotating access keys`.

9. Check key count and policy attachment.
   Each user gets one line with `keys`, `boundary` and `policy`; a difference returns exit code 1.

   ```sh
   scripts/aws-memory.sh check --stage dev --profile tenant-ec2-admin
   ```

This check compares the attached boundary with `BoundaryArn`.
It checks for the person inline policy or a customer managed service policy.
It does not compare policy contents, simulate namespace access, or verify that a key is active.

10. Render a person's client config.
   This calls `aws-memory.py client-config` with `--stage`, `--actor` and the saved `--memory-id`.

   ```sh
   scripts/aws-memory.sh client-config --stage dev --actor alice
   ```

Hand the key and client config to the person through a password-protected Bitwarden Send.
Set one access and one day. Give the password through a separate channel.
The recipient stores the key under the profile named by the client config, with file mode `0600`.
The client config holds no key. Never put a key in this repository to prepare the Send.
See [Send privacy](https://bitwarden.com/help/send-privacy/) and [Send lifespan](https://bitwarden.com/help/send-lifespan/).

11. Preview a stack deletion.
    The script prints the retained memory and bucket before the delete command.

    ```sh
    scripts/aws-memory.sh delete-stack --stage dev --profile tenant-ec2-admin
    ```

12. Delete a production stack only after separate approval.
    Production requires `--i-know` as well as `--apply` and an explicit `--profile`.

    ```sh
    scripts/aws-memory.sh delete-stack --stage prod --i-know --apply --profile tenant-ec2-admin
    ```

Delete each user's access keys separately before stack deletion or offboarding through CloudFormation.
Keys created outside CloudFormation can prevent deletion of an IAM user.
The script does not delete records, bucket objects or earlier hand-made memory users.
See [Deleting an IAM user](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_users_manage.html#id_users_deleting).

Warning: retained memory and bucket resources can continue to incur charges after stack deletion.

## Verification limits

A deploy of the dev stage proved the two deploy steps: the prepare step made one change set, and the execute step ran only that change set.
That deploy created each of the 12 resources of the dev template.
On the dev stage, key creation with credential sync and the key check passed for each user.
[What the dev stage proved](../memory-stack.md#what-the-dev-stage-proved) lists each fact and the live access tests.
The execute step accepted the change set of that deploy with an empty `AdministratorUser`.
Not verified: the exact form of an empty `AdministratorUser` value in the change set reply. The script reads a missing value as empty and refuses any other difference.
Not verified: a deploy of the test or prod stage, an update, a rotation, offboarding or a stack deletion.
Not verified: end-to-end use of the rendered client config with tenant-pi's stage-specific user prefix.
Not verified: a complete Bitwarden Send handover.
The offline script tests use isolated AWS and credential-sync stubs. They make no AWS call and create no real key.
