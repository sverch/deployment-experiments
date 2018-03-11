#!/usr/bin/env python

import attr
import boto3
import time

from subnet_generator import generate_subnets
from datacenter import Datacenter
from exceptions import NotEnoughIPSpaceException
from exceptions import OperationTimedOut
from exceptions import BadEnvironmentStateException


@attr.s
class Network(object):
    """
    A network object.

    Represents a collection of subnets that services can be spun up in.

    This layer is also where the NACLs, internet gateways, and routing tables
    will be managed.
    """
    provider = attr.ib(default="aws")
    retry_count = attr.ib(default="60")
    retry_delay = attr.ib(default="1.0")

    def parse_name(self, name):
        """
        Given a full network name, return (vpc_name, network_name).
        """
        (vpc_name, network_name) = tuple(name.split("."))
        return (vpc_name, network_name)

    def carve_subnets(self, vpc_info, prefix=28, count=3):
        ec2 = boto3.client("ec2")

        # Get existing subnets, to make sure we don't overlap CIDR blocks
        existing_subnets = ec2.describe_subnets(Filters=[{
                'Name': 'vpc-id',
                'Values': [vpc_info["Id"]]}])
        existing_cidrs = [subnet["CidrBlock"]
                          for subnet in existing_subnets["Subnets"]]

        # Finally, iterate the list of all subnets of the given prefix that can
        # fit in the given VPC
        subnets = []
        for new_cidr in generate_subnets(vpc_info["CidrBlock"],
                                         existing_cidrs, prefix):
            subnets.append(str(new_cidr))
            if len(subnets) == count:
                return subnets
        raise NotEnoughIPSpaceException("Could not allocate %s subnets with "
                                        "prefix %s in vpc %s",
                                        (count, prefix, vpc_info["Id"]))

    def get_availability_zones(self):
        ec2 = boto3.client("ec2")
        try:
            availability_zones = ec2.describe_availablity_zones()
            return [az["ZoneName"]
                    for az in availability_zones["AvailabilityZones"]]
        except Exception:
            # TODO: XXX: Moto does not have this function supported...  So I
            # need to fix that before this code can be reasonable again,
            # because otherwise it just fails.
            return ["us-east-1a", "us-east-1b", "us-east-1c"]

    def aws_provision_subnet(self, name, subnet_cidr, availability_zone,
                             dc_id):
        """
        Provision a single subnet with a route table and the proper tags.
        """
        ec2 = boto3.client("ec2")
        vpc_name, network_name = self.parse_name(name)
        subnet = ec2.create_subnet(CidrBlock=subnet_cidr,
                                   AvailabilityZone=availability_zone,
                                   VpcId=dc_id)
        subnet_id = subnet["Subnet"]["SubnetId"]
        route_table = ec2.create_route_table(VpcId=dc_id)
        route_table_id = route_table["RouteTable"]["RouteTableId"]
        ec2.associate_route_table(RouteTableId=route_table_id,
                                  SubnetId=subnet_id)
        try:
            creation_retries = 0
            while creation_retries < self.retry_count:
                try:
                    ec2.create_tags(Resources=[subnet_id],
                                    Tags=[{"Key": "cloud-deployer-deployment",
                                           "Value": vpc_name},
                                          {"Key": "cloud-deployer-network",
                                           "Value": network_name}])
                    subnet_ids = [subnet_info["Id"]
                                  for subnet_info in self.aws_discover(name)]
                    if subnet_id not in subnet_ids:
                        time.sleep(float(self.retry_delay))
                    else:
                        break
                except Exception:
                    time.sleep(float(self.retry_delay))
                    creation_retries = creation_retries + 1
                    if creation_retries >= self.retry_count:
                        raise OperationTimedOut(
                                "Cannot find created Subnet: %s" % subnet_id)
        except Exception, e:
            ec2.delete_subnet(SubnetId=subnet_id)
            ec2.delete_route_table(RouteTableId=route_table_id)
            raise e
        return self.canonicalize_subnet_info(subnet["Subnet"])

    def aws_provision(self, name):
        """
        Provision the subnets with AWS.

        This actually has a few steps.

        1. Get ID of VPC to provision subnets in, or create if nonexistent.
        2. Create subnets across availability zones.
        """
        # 1. Get ID ov VPC to provision subnets in.
        vpc_name, network_name = self.parse_name(name)
        dc = Datacenter()
        dc_info = dc.discover(vpc_name)
        if not dc_info:
            dc_info = dc.provision(vpc_name)

        # 2. Create subnets across availability zones.
        subnets_info = []
        availability_zones = self.get_availability_zones()
        for subnet_cidr, availability_zone in zip(self.carve_subnets(dc_info),
                                                  availability_zones):
            try:
                subnet_info = self.aws_provision_subnet(name, subnet_cidr,
                                                        availability_zone,
                                                        dc_info["Id"])
            except Exception, e:
                self.destroy(name)
                raise e
            subnets_info.append(subnet_info)
        return subnets_info

    def add_path(self, path):
        # This doesn't actually do anything for networks, since all route
        # tables have the "local" route in AWS.  It would only matter if I
        # implement NACLs.
        pass

    def expose(self, name):
        """
        Create an internet gateway for this network and add routes to it for
        all subnets.

        Steps:

        1. Discover current VPC.
        2. Create and attach internet gateway only if it doesn't exist.
        4. Add route to it from all subnets.
        """
        ec2 = boto3.client("ec2")

        # 1. Discover current VPC.
        vpc_name, network_name = self.parse_name(name)
        dc = Datacenter()
        dc_id = dc.discover(vpc_name)["Id"]

        # 2. Create and attach internet gateway only if it doesn't exist.
        # Apparently it's an error to try to attach two gateways to the same
        # VPC.
        igw = ec2.describe_internet_gateways(
                Filters=[{'Name': 'attachment.vpc-id', 'Values': [dc_id]}])
        if len(igw["InternetGateways"]) == 1:
            igw_id = igw["InternetGateways"][0]["InternetGatewayId"]
        elif len(igw["InternetGateways"]) == 0:
            igw = ec2.create_internet_gateway()
            igw_id = igw["InternetGateway"]["InternetGatewayId"]
            ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=dc_id)
        else:
            raise Exception(
                    "Invalid response from describe_internet_gateways: %s" %
                    igw)

        # 3. Add route to it from all subnets.
        subnet_ids = [subnet_info["Id"] for subnet_info in self.discover(name)]
        for subnet_id in subnet_ids:
            subnet_filter = {
                    'Name': 'association.subnet-id',
                    'Values': [subnet_id]}
            route_tables = ec2.describe_route_tables(Filters=[subnet_filter])
            if len(route_tables["RouteTables"]) != 1:
                raise Exception("Expected to find exactly one route table: %s",
                                route_tables)
            route_table = route_tables["RouteTables"][0]
            ec2.create_route(RouteTableId=route_table["RouteTableId"],
                             GatewayId=igw_id,
                             DestinationCidrBlock="0.0.0.0/0")

    def aws_discover(self, name):
        ec2 = boto3.client("ec2")
        vpc_name, network_name = self.parse_name(name)
        service_filter = {'Name': "tag:cloud-deployer-network",
                          'Values': [network_name]}
        deployment_filter = {'Name': "tag:cloud-deployer-deployment",
                             'Values': [vpc_name]}
        subnets = ec2.describe_subnets(Filters=[service_filter,
                                                deployment_filter])
        return [self.canonicalize_subnet_info(subnet)
                for subnet in subnets["Subnets"]]

    def provision(self, name="default"):
        if self.provider == "aws":
            if self.discover(name):
                raise "Service %s already exists!" % name
            return self.aws_provision(name)
        else:
            raise NotImplemented

    def discover(self, name):
        if self.provider == "aws":
            return self.aws_discover(name)
        else:
            raise NotImplemented

    def internet_gateway_route_count(self, dc_id, igw_id):
        ec2 = boto3.client("ec2")
        count = 0
        vpc_id_filter = {'Name': 'vpc-id', 'Values': [dc_id]}
        route_tables = ec2.describe_route_tables(Filters=[vpc_id_filter])
        for route_table in route_tables["RouteTables"]:
            for route in route_table["Routes"]:
                if "GatewayId" in route and route["GatewayId"] == igw_id:
                    count = count + 1
        return count

    def delete_subnet(self, subnet_id):
        ec2 = boto3.client("ec2")
        deletion_retries = 0
        while deletion_retries < self.retry_count:
            try:
                ec2.delete_subnet(SubnetId=subnet_id)
            except ec2.exceptions.ClientError as e:
                if e.response['Error']['Code'] == 'DependencyViolation':
                    # A dependency violation might be transient if
                    # something is being actively deleted by AWS, so sleep
                    # and retry if we get this specific error.
                    time.sleep(float(self.retry_delay))
                elif e.response['Error']['Code'] == 'InvalidSubnetID.NotFound':
                    # Just return successfully if the subnet is already gone
                    # for some reason.
                    return
                else:
                    raise e
                deletion_retries = deletion_retries + 1
                if deletion_retries >= self.retry_count:
                    raise OperationTimedOut(
                            "Failed to delete subnet: %s" % str(e))

    def aws_destroy(self, name):
        """
        Destroy all networks represented by this object.  Also destroys the
        underlying VPC if it's empty.

        Steps:

        1. Discover the current VPC.
        2. Destroy route tables.
            2.a. Disassociate and delete route table.
            2.b. Delete non referenced internet gateways.
        3. Delete all subnets.
        4. Delete VPC if it has no more subnets, but wait until these are
           deleted.
        """
        ec2 = boto3.client("ec2")
        subnet_ids = [subnet_info["Id"] for subnet_info in self.discover(name)]

        # 1. Discover the current VPC.
        vpc_name, network_name = self.parse_name(name)
        dc = Datacenter()
        dc_id = dc.discover(vpc_name)["Id"]

        # 2. Destroy route tables.
        def delete_route_table(route_table):
            # 2.a. Disassociate and delete route table.
            associations = route_table["Associations"]
            for association in associations:
                ec2.disassociate_route_table(
                        AssociationId=association["RouteTableAssociationId"])
            if (len(associations) == 1 or len(associations) == 0):
                ec2.delete_route_table(
                        RouteTableId=route_table["RouteTableId"])
            # 2.b. Delete non referenced internet gateways.
            routes = route_table["Routes"]
            for route in routes:
                if "GatewayId" in route and route["GatewayId"] != "local":
                    igw_id = route["GatewayId"]
                    if not self.internet_gateway_route_count(dc_id, igw_id):
                        ec2.detach_internet_gateway(InternetGatewayId=igw_id,
                                                    VpcId=dc_id)
                        ec2.delete_internet_gateway(InternetGatewayId=igw_id)

        for subnet_id in subnet_ids:
            subnet_filter = {
                    'Name': 'association.subnet-id',
                    'Values': [subnet_id]}
            route_tables = ec2.describe_route_tables(Filters=[subnet_filter])
            if len(route_tables["RouteTables"]) > 1:
                raise BadEnvironmentStateException(
                        "Expected to find at most one route table associated "
                        "with: %s, output: %s" % (subnet_id, route_tables))
            if len(route_tables["RouteTables"]) == 1:
                delete_route_table(route_tables["RouteTables"][0])

        # 3. Delete all subnets.
        for subnet_id in subnet_ids:
            self.delete_subnet(subnet_id)

        # 4. Delete VPC if it has no more subnets, but wait until these are
        # deleted.
        remaining_subnets = ec2.describe_subnets(
                Filters=[{'Name': 'vpc-id',
                          'Values': [dc_id]}])
        remaining_subnet_ids = [subnet["SubnetId"] for subnet
                                in remaining_subnets["Subnets"]]
        retries = 0
        while (any(i in subnet_ids for i in remaining_subnet_ids)
               and retries < 720):
            remaining_subnets = ec2.describe_subnets(
                    Filters=[{'Name': 'vpc-id',
                              'Values': [dc_id]}])
            remaining_subnet_ids = [subnet["SubnetId"] for subnet
                                    in remaining_subnets["Subnets"]]
            retries = retries + 1
            time.sleep(1)
        if len(remaining_subnets["Subnets"]) == 0:
            dc.destroy(vpc_name)

    def destroy(self, name):
        if self.provider == "aws":
            return self.aws_destroy(name)
        else:
            raise NotImplemented

    def canonicalize_subnet_info(self, subnet):
        return {
            "Id": subnet["SubnetId"],
            "CidrBlock": subnet["CidrBlock"]
        }

    def do_list(self):
        ec2 = boto3.client("ec2")

        def get_deployment_tag(subnet):
            if "Tags" not in subnet:
                return None
            for tag in subnet["Tags"]:
                if tag["Key"] == "cloud-deployer-deployment":
                    return tag["Value"]
            return None

        def get_network_tag(subnet):
            if "Tags" not in subnet:
                return None
            for tag in subnet["Tags"]:
                if tag["Key"] == "cloud-deployer-network":
                    return tag["Value"]
            return None

        subnet_info = {}
        subnets = ec2.describe_subnets()
        for subnet in subnets["Subnets"]:
            dc_name = get_deployment_tag(subnet)
            network_name = get_network_tag(subnet)
            if dc_name not in subnet_info:
                subnet_info[dc_name] = {}
            if network_name not in subnet_info[dc_name]:
                subnet_info[dc_name][network_name] = []
            subnet_info[dc_name][network_name].append(
                    self.canonicalize_subnet_info(subnet))
        return subnet_info
