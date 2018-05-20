# Deployment Experiments

An experimental tool that should abstract away some of the things that a person
shouldn't have to worry about.  By doing that, it should also make it easier to
build portable infrastructure across different cloud platforms as a side effect.

## Usage

### Datacenter (VPC)

    dc = Datacenter()
    dc_id = dc.provision("unittest_foo")["Id"]
    dc.do_list()
    dc_cidr = dc.discover("unittest_foo")["CidrBlock"]
    dc.destroy("unittest_foo")

### Network (Subnets)

    net = Network()
    subnets = net.provision("example.service")
    subnet_ids = [subnet["Id"] for subnet in subnets]
    net.do_list()
    net.discover("example.service")
    net.destroy("example.service")

### Service (Instances)

    user_data = """#cloud-config
    repo_update: true
    repo_upgrade: all
    packages:
      - nginx
    runcmd:
      - service nginx start"""

    image = VirtualMachine(user_data=user_data, plugins=[])

    # Create the provisioner objects
    lb = LoadBalancer()
    web = Service()
    dns = ServiceDns()

    # Provision all the resources
    lb.provision("example.lb")
    web.provision("example.service", lb.discover("example.lb")["Id"], image)
    dns.provision("foo.myexamplesite.com",
                  lb.discover("example.lb")["DNSName"])

    # Deal with networking
    lb.expose("example.lb")
    web.allow("example.service", "example.lb")

    # Make sure they are gone when I destroy them
    lb.destroy("example.lb")
    dns.destroy("foo.myexamplesite.com")
    web.destroy("example.service")

## Testing

This depends on pipenv (https://docs.pipenv.org/):

```
./test
```

## Links

- https://github.com/hashicorp/terraform
- https://github.com/juju/juju
- https://libcloud.apache.org/
- https://github.com/APIs-guru/openapi-directory
- https://cloudstack.apache.org/
