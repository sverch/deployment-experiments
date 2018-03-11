#!/usr/bin/env python

from deployment_experiments.instance_fitter import InstanceFitter


def test_datacenter():
    instance_fitter = InstanceFitter()

    # If no memory, cpu, or storage is passed in, find the cheapest.
    assert instance_fitter.get_fitting_instance() == "t2.micro"
