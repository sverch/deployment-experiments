Here's what I think I've learned about the layers of abstraction.

- Datacenter
- Networking
- Service
- Application

So the Datacenter is the base layer.  This is essentially the VPC, to which
everything is attached.

The next is the networking.  This should always create three subnets balanced
across availability zones (or maybe this could be configurable).

The next is the service layer.  This would be things like load balancers and
machinge images, that would be deployed in the subnets.

Then finally is the application layer.  I think for the mve this would just be
part of the service layer.  But you can deploy things in the service layer that
has a nice application llayer, like Kubernetes or Mesos.

The idea is that each layer is NOT modifiable.  It is only modifiable by
replacement.

|- Datacenter
  |- Networking
     |- Service
       |- Application

    d = Datacenter()
    n = d.create_network()
    s = n.create_service()
    d.deploy()
