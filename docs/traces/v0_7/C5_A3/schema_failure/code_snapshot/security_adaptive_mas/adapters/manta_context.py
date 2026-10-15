"""Read-only MANTA projection; authority stays in the directed runtime."""
from .._vendor.manta.MAS.self_evolved.spec import AgentNode, ContextPolicy, GroupSpec, TopologySpec
from .._vendor.manta.MAS.self_evolved.context import SharedContextController


class MantaContextAdapter:
    def __init__(self, runtime, trace):
        self.runtime,self.trace=runtime,trace
        self.sidecars={}

    def project(self, graph, stored_authors=()):
        nodes=[]
        for agent in graph.agents:
            incoming={a for a,b in graph.extra_edges if b==agent.agent_id}
            incoming.add(agent.agent_id)
            incoming.update(author for author,consumer in stored_authors if consumer==agent.agent_id)
            nodes.append(AgentNode(agent.agent_id,"runtime_view",allowed_tools=tuple(sorted(agent.allowed_tools)),
                context=ContextPolicy(visible_from=tuple(sorted(incoming)),evidence_access="own",
                                      summary_only=False,max_packet_chars=0)))
        existing={n.agent_id for n in nodes}
        for author,consumer in stored_authors:
            if author not in self.runtime.instances:
                raise PermissionError("unknown_stored_author")
            if author not in existing:
                nodes.append(AgentNode(author,"runtime_view",allowed_tools=(),persona_hint="historical read-only author"))
                existing.add(author)
        group=GroupSpec("runtime_view","singleton" if len(nodes)==1 else "star",
                        tuple(n.agent_id for n in nodes),leader_id="agent_0" if len(nodes)>1 else None)
        spec=TopologySpec(graph.version,tuple(nodes),(group,),"runtime_view",tuple(sorted(graph.extra_edges)))
        spec.validate(max_agents=len(nodes))
        return spec

    def select_packets(self, graph, candidate_packets, *, consumer_id):
        if graph is not self.runtime.graph:
            raise PermissionError("stale_manta_projection")
        authorized=[]
        stored=[]
        for packet in candidate_packets:
            meta=self.sidecars[packet["message_id"]]
            ref=meta["ref"]
            if meta["graph_version"]!=graph.version or packet["sender"]!=self.runtime.objects[ref.key]["writer"]:
                raise PermissionError("packet_identity_or_version_mismatch")
            if packet["recipients"] != [consumer_id]:
                raise PermissionError("packet_recipient_mismatch")
            transport=self.runtime.check_delivery(consumer_id,ref)
            if transport=="persistent_store":stored.append((packet["sender"],consumer_id))
            authorized.append(packet)
        spec=self.project(graph,stored)
        selected=SharedContextController(spec).visible_packets({"messages":authorized},agent_id=consumer_id,latest_only=False)
        self.trace.log("manta_packet_selection",graph_version=graph.version,
            source_version="3da40879d690a25459cc401292e5c6f0cc366d84",
            input_packet_ids=[p["message_id"] for p in candidate_packets],selected_ids=[p["message_id"] for p in selected])
        for packet in selected:
            self.runtime.check_delivery(consumer_id,self.sidecars[packet["message_id"]]["ref"])
        return selected
