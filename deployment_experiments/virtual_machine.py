#!/usr/bin/env python
"""
VirtualMachine

This is the abstraction that defines a deployment unit of a virtual machine (as
opposed to a container, a function, or other artifact).

So what value does this provide?

Well, the VirtualMachine class should really just be an interface, that the
rest of this framework uses.  The minimum functionality is separating bake
scripts and runtime scripts.

I still don't quite know how all the port mapping stuff will work.

The interface I'm looking for:

I take an image, and I specify what I want on it.  So I can say "I want sssd"
and the library will do the right thing to figure out which part needs to
happen at runtime versus build time.

Ok, so what is MVE?

Plugin only supports ansible+git, and we don't worry about deploy keys (assume
everything is open source).
"""

import attr
import boto3

@attr.s
class VirtualMachineBuilderInterface(object):
    """
    Interface for building an image.  This should actually go to the underlying
    provider and create an image.
    """
    def build_image(self):
        raise NotImplemented

@attr.s
class PackerImageBuilder(VirtualMachineBuilderInterface):
    """
    Builds an image using Packer.  In theory, I could support other backends,
    but packer works.  No reason not to punt there for now until I figure out a
    better way.
    """
    packer_configuration = attr.ib(type=list)

    def build_image(self):
        # 1. Clone Repos?
        # 2. Run Packer
        # 3. Return AMI id
        return "ami-66506c1c"

@attr.s
class AnsibleCloudInitGenerator(object):
    ansible_playbook = attr.ib()

    def get_runtime_script(self):
        # This is garbage, just a POC to see how it looks.  Eventually I'd
        # actually have a template file, or even a script to which I would pass
        # args.
        return """
#!/bin/bash
sudo mkdir /opt/ansible-cloud-init
sudo chmod 777 /opt/ansible-cloud-init
git clone %s /opt/ansible-cloud-init/repo
cd /opt/ansible-cloud-init/repo
ansible-playbook playbook.yml
""" % self.ansible_playbook


@attr.s
class VirtualMachinePlugin(object):
    build_source = attr.ib()
    run_source = attr.ib()

    def build_scripts(self):
        return { "type": "packer", "contents": self.build_source }

    def runtime_scripts(self):
        return AnsibleCloudInitGenerator(self.run_source).get_runtime_script()

@attr.s
class VirtualMachine(object):
    """
    An abstraction for a virtual machine, in an attempt to capture the two
    phases of the life of a VM.

    First, there's the build stage, which contains generic scripts needed to set
    up a machine.  How can I represent this?

    I mean, I could just use packer.  That's specifically designed for this
    purpose.  This could just be a wrapper around packer, or packer could be a
    plugin.
    """
    plugins = attr.ib(type=list)
    provider = attr.ib(default="aws")

    def build_cloud_init(self):
        runtime_scripts = []
        for plugin in self.plugins:
            runtime_scripts.extend(plugin.runtime_scripts())
        return "%cloud-init%".join(runtime_scripts)

    def build(self):
        build_scripts = []
        for plugin in self.plugins:
            build_scripts.extend(plugin.build_scripts()["contents"])
        return PackerImageBuilder(build_scripts).build_image()

    def get(self):
        # TODO: Maybe I should not rebuild the AMI every time this is called?
        # Who should cache that/search for it?
        return self.build()
