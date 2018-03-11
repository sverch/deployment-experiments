# What does this do?

It's a collection of standalone tools that make it easier to deploy
infrastructure dynamically.

Why is it different?  Well it's trying to do what Juju and CoreOS are doing, but
in a way that's less of a platform that you have to invest in and more of a
useful set of tools.

I want to try to get to the point where I can call this "Servers for Humans".
It's meant to get to the esssence of what it means to deploy infrastructure,
without anything that a human shouldn't have to think about.

## Components

- A thing that makes it easier to deploy servers in any platform, essentially a
  wrapper around auto scaling groups.
  - How to deal with scaling triggers?
  - How to deal with monitoring?
- A thing that automatically figures out your firewall and routing rules based
  on a simple dependency graph.
- A thing that generates the right user data or packer configuration based on
  what you give it.  Not sure how this would work yet.

## Testing

This depends on pipenv (https://docs.pipenv.org/):

```
./test
```

Eventually I want to do this using a more standard tool, like tox, but that's
much more involved.  See the script for more details.

## Basic Usage

First is the datacenter object.  All that does is spin up and down VPCs.  From
a python shell, run:

```
from deployment_experiments.datacenter import Datacenter
dc = Datacenter(deployment_name="dev")
dc1_id = dc.create()
dc.discover(dc1_id)
dc.destroy(dc1_id)
```

Next is the Network object, which will spin up three subnets across AZs, and do
the necessary fitting to fit them into your datacenter.


```
from deployment_experiments.network import Network
net = Network(deployment_name="prod")
net.provision("web")
net.discover("web")
net.provision("db", colocated_network="web")
net.destroy("web")
net.destroy("db")
```

The `colocated_network` argument says to use the same VPC as the "web" network.

## Design Principles

### All Dynamic

The source of truth should be the cloud, or some kind of inventory.  You should
not ever have any static declarations of what your infrastructure looks like.
In fact, that should be impossible without extra work.

### User Friendly

Try to be like the person who wrote the requests library for python and pyenv.
Make this library "for humans".

### Correct Abstractions

Think very carefully about the abstractions, and make sure that we're using the
correct one.  Is a container the right abstraction?  What about a server?  How
do we want to express networking rules?

### Make It Hard To Do The Wrong Thing

I'm sick of arguing with people that single points of failure are bad.  This
tool should explicitly not support antipatterns.

### Teach The Why

A corollary to being strict and preventing bad behaviour means that the tool
has to help explain why the behaviour is bad.  If something isn't allowed but
the error message is too generic for people to understand why, that's a bug.

### People Are Lazy

No one will take the time to understand what's actually going on.  Just make the
thing do what it should.

### Unix Philosopy

Each component should be useful on its own without using any of the other
components.

## Links

- https://github.com/hashicorp/terraform
- https://github.com/juju/juju
- https://libcloud.apache.org/
- https://github.com/APIs-guru/openapi-directory
- https://cloudstack.apache.org/
