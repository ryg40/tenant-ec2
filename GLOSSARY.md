# tenant-ec2

The words of the install kit, its core and its options.
Each page uses these words with the meanings below.

## Language

### Install kit and services

**Install kit**:
The files and instructions that build a host from pinned kits.
It includes a core and optional parts.
_Avoid_: deployment bundle, host package

**Core**:
The parts that every install needs, without any option.
_Avoid_: base bundle, default tier

**Option**:
A part that stays off until the person who installs the kit selects it.
_Avoid_: add-on, extension pack

**Kit**:
One service with its build command and verify command, taken from its own source at a pinned tag.
_Avoid_: service package, service module

**Lock**:
The record of each kit's source tag, commit and built image.
_Avoid_: pin manifest, pin file

**Compose stack**:
The services that the core Compose file and the selected overlays start together on the host.
_Avoid_: application stack, single-file stack

**Overlay**:
A Compose file that adds an option or an edge to the core Compose file.
It is not a complete Compose model alone.
_Avoid_: override bundle, standalone overlay

**Gateway**:
The service that receives each model call from the other services.
It authenticates each model request and selects the provider route.
_Avoid_: model proxy, model router

**Virtual key**:
A credential that the gateway gives to one service for a limited set of model routes.
_Avoid_: gateway token, provider key substitute

### Host and stack

**Host**:
The EC2 instance that runs the Compose stack.
A replacement uses the retained address and storage after their previous attachments are released.
_Avoid_: cloud box, cloud node

**Stack**:
The CloudFormation stack that makes the host, network, storage and identity.
_Avoid_: cloud environment, infrastructure bundle

**Provisioner**:
The scoped AWS identity that makes or changes the stack from the development machine.
It is not an administrator.
_Avoid_: deploy user, admin key

**Boundary**:
The permissions limit that each instance role must obey in addition to its own policy.
_Avoid_: guard policy, limit policy

**Instance role**:
The AWS identity that gives the host its AWS rights without a stored access key.
_Avoid_: host key, machine account

**User data**:
The initial script that a new instance runs to obtain the install kit and start host preparation.
_Avoid_: init script, startup payload

**Bootstrap**:
The script that brings a prepared host to a running Compose stack.
A second run keeps the same result unless the inputs change.
_Avoid_: setup script, boot installer

**Check script**:
The script that runs source checks, local container checks and offline regression tests over the install kit.
_Avoid_: lint script, test launcher

**Verifier**:
The script that checks deployed containers and internal endpoints for the selected Compose file set.
It makes no model call and proves no public login.
_Avoid_: deployment smoke, live kit check

**Operations**:
The optional daily data-volume snapshots, host logs and billing alarm.
The private bucket has a separate switch.
_Avoid_: monitoring bundle, operations tier

### Storage

**Data volume**:
The block storage of the host for databases, agent state and optional edge or Copilot state.
It outlives the instance.
_Avoid_: data disk, database disk

**File system**:
The shared storage of the stack for OpenKnowledge, OpenViking, uploads and exports.
More than one instance can use it.
_Avoid_: network drive, shared disk

**Access point**:
An entry into the file system with its own directory and POSIX identity.
_Avoid_: storage share, shared mount

**Durable mount**:
A mounted data volume or access point that must be available before Docker starts.
The host needs four durable mounts.
_Avoid_: persistent folder, storage directory

**Snapshot**:
A copy of the data volume at one point in time.
The word alone does not mean a portable snapshot.
_Avoid_: volume image, disk export

### Networks and edge

**Service network**:
A private Compose network that joins only services that call each other.
_Avoid_: stack network, service backend

**Egress network**:
A Compose network that gives selected services a path to the internet.
_Avoid_: outbound network, public service network

**Edge network**:
A Compose network that joins the edge to a selected service.
_Avoid_: frontend network, public backend

**Edge**:
The services that end public TLS connections and send requests to selected targets.
The core edge is the default; the Pangolin edge is an option.
_Avoid_: ingress layer, perimeter proxy

**Core edge**:
The default edge that serves the selected names without a tunnel service or a DNS provider key.
_Avoid_: basic proxy, fallback edge

**Resource**:
A service that the edge serves under one name of the domain subtree.
A CloudFormation component is a template resource, not a resource alone.
_Avoid_: edge app, published backend

**Gate**:
The Entra login and group check in front of OpenKnowledge with either edge.
A service outside this gate checks its own keys and edge rules.
_Avoid_: auth proxy, SSO box

**Domain subtree**:
The part of a domain below which each resource has its name.
_Avoid_: base domain, host zone

**Blueprint**:
The declaration of the Pangolin resources and their targets.
_Avoid_: resource configuration, dashboard setup

**Site**:
A network that Pangolin uses as the location of a resource target.
It can be on the host or across a tunnel.
_Avoid_: tunnel peer, remote cluster

**Newt**:
The Pangolin tunnel client that runs inside a site and starts only outbound connections.
_Avoid_: site agent, tunnel connector

**Home lab**:
An optional private network outside the cloud that joins Pangolin as a site.
_Avoid_: on-prem lab, private cloud annex

### Credentials and publication

**Environment file**:
The private file of named settings and credentials on the development machine or host.
It never enters Git.
_Avoid_: dotenv file, environment config

**Sample environment file**:
The tracked example that defines the setting names without secret values.
_Avoid_: default file, sample dotenv

**Secrets Manager secret**:
The AWS object that holds the host settings and credentials from the environment file.
The instance role reads it for the bootstrap.
_Avoid_: environment parameter, cloud vault entry

**Portable snapshot**:
A commit that holds only the reviewed publish set, without development history.
The host clones it at its tag and checks its commit.
_Avoid_: repository mirror, history export

**Publish set**:
The reviewed list of files that a portable snapshot holds.
_Avoid_: public files list, publish allowlist
