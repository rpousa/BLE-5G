# nrlink-run plus iptables/conntrack: the AGVs layer-3 relay sidecars (NAT and routes in the UE netns).
# The LAN blocks port 80: apt goes over https.
FROM nrlink-run:local
RUN sed -i -e s,http://archive.ubuntu.com,https://mirrors.edge.kernel.org, -e s,http://security.ubuntu.com,https://mirrors.edge.kernel.org, /etc/apt/sources.list.d/ubuntu.sources && apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends iptables conntrack iproute2 && rm -rf /var/lib/apt/lists/*
