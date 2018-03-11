from deployment_experiments import routegraph

# TODO: Do the next steps of this.
#
# This is the simplest case, but I also want to make this availability zone
# aware.  I suppose I won't be able to round trip as well.  Do I have to make
# it availability zone aware though?  It's worth playing around with to figure
# out what interface would be nice.
#
# Also, adding protocol information to these connections would allow for
# combining the firewall and routing rules.  Is that actually a good idea?
# Perhaps that's breaking an abstraction.  Anything that is capable of doing
# that could actually use this underneath and be easy to implement.  So perhaps
# just stick with this for now, and then there could be a POC that uses both of
# these.
net = [["0", "1", "external"]]

routes = {
        "0": [{
            "destination": "external",
            "target": "1"
            }],
        "1": [{
            "destination": "0",
            "target": "0"
            },
            {
            "destination": "external",
            "target": "external"
            }]
        }


def test_net_to_routes():
    assert routes == routegraph.net_to_routes(net)


def test_routes_to_net():
    assert net == routegraph.routes_to_net(routes)
