#!/usr/bin/env python

import attr
import boto3

from subnet_generator import generate_subnets


@attr.s
class DatacenterInventory(object):
    """
    Represents a list of datacenters.  It can be created with a set list, or
    configured to discover from a given provider.
    """
    provider = attr.ib(default="aws")
    datacenters = attr.ib(type=list, default=None)
    region = attr.ib(default="all")
    deployment_name = attr.ib(default="default")

    def get(self):
        if self.datacenters:
            return self.datacenters
        else:
            return self.discover()

    def discover(self):
        # TODO: Factor in region.  Right now I discover all VPCs in the region
        # configured in my aws settings, but I want to be more explicit (either
        # discover all VPCs in this account, or all VPCs in a geographic
        # region).  I don't want this to encourage working with VPCs directly.
        if self.provider == "aws":
            ec2 = boto3.client("ec2")
            deployment_filter = {'Name': "tag:cloud-deployer-deployment",
                                 'Values': [self.deployment_name]}
            return [vpc["VpcId"] for vpc
                    in ec2.describe_vpcs(Filters=[deployment_filter])["Vpcs"]]
        else:
            return []


@attr.s
class Datacenter(object):
    """
    A datacenter object.

    Use cases:
        1. Create a new DC, not overlapping other ranges.
        2. Discover an existing DC.
        3. Express an external DC.
    """
    provider = attr.ib(default="aws")
    siblings = attr.ib(type=DatacenterInventory, default=DatacenterInventory())
    prefix = attr.ib(default=16)
    deployment_name = attr.ib(default="default")

    def aws_create(self, private_block="10.0.0.0/8"):
        # TODO: I think this throws an exception, but figure out proper error
        # handling.
        ec2 = boto3.client("ec2")
        existing_cidrs = []
        if self.siblings:
            sibling_ids = [dc_id for dc_id in self.siblings.get()]
            dc = Datacenter()
            for sibling_id in sibling_ids:
                sibling_dc = dc.discover(sibling_id)
                existing_cidrs.append(sibling_dc["Vpcs"][0]["CidrBlock"])
        new_cidr = str(generate_subnets(private_block,
                                        existing_cidrs,
                                        self.prefix).next())
        vpc = ec2.create_vpc(CidrBlock=new_cidr)
        vpc_id = vpc["Vpc"]["VpcId"]
        ec2.create_tags(Resources=[vpc_id],
                        Tags=[{"Key": "cloud-deployer-deployment",
                               "Value": self.deployment_name}])

        # TODO: Figure out whether I really want this.  Should every DC have an
        # internet gatway by default?  Doesn't AWS already do that?
        igw = ec2.create_internet_gateway()
        igw_id = igw["InternetGateway"]["InternetGatewayId"]
        ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
        return vpc_id

    def aws_discover(self, dc_id):
        # TODO: I think this throws an exception, but figure out proper error
        # handling.
        ec2 = boto3.client("ec2")
        return ec2.describe_vpcs(Filters=[{"Name": "vpc-id",
                                           "Values": [dc_id]}])

    def create(self, private_block="10.0.0.0/8"):
        if self.provider == "aws":
            return self.aws_create(private_block)
        else:
            raise NotImplemented

    def discover(self, dc_id):
        if self.provider == "aws":
            return self.aws_discover(dc_id)
        else:
            raise NotImplemented

    def destroy(self, dc_id):
        ec2 = boto3.client("ec2")
        # TODO: Figure out whether I really want this.  Should every DC have an
        # internet gatway by default?  Doesn't AWS already do that?
        # XXX: Moto hasn't implemented filters on this function yet...
        # igw_ids = [igw["InternetGatewayId"] for igw in
        # ec2.describe_internet_gateways(
        #           Filters=[{"Name": "vpc-id",
        #                     "Values": [dc_id]}])["InternetGateways"]]
        igw_ids = [igw["InternetGatewayId"] for igw
                   in ec2.describe_internet_gateways()["InternetGateways"]
                   if dc_id in [attachment["VpcId"]
                                for attachment in igw["Attachments"]]]
        for igw_id in igw_ids:
            ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=dc_id)
            ec2.delete_internet_gateway(InternetGatewayId=igw_id)
        return ec2.delete_vpc(VpcId=dc_id)
