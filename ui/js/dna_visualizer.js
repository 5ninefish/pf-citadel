// PeakForge Station — Nervous System DNA Graph
// D3 v7 force-directed visualization of gbrain nodes + edges

const GROUP_COLORS = {
    eod:          "#00ff9d",
    scavenge:     "#00d4ff",
    architecture: "#ff9d00",
    decision:     "#ff4757",
    signal:       "#ffa502",
    horizon:      "#a29bfe",
    wiki:         "#74b9ff",
    skill:        "#fd79a8",
    concept:      "#636e72",
    reference:    "#ffffff",
};

let dnaLoaded = false;

async function loadDNA() {
    if (dnaLoaded) return;

    const svg = d3.select("#dna-graph");
    const w = svg.node().getBoundingClientRect().width || 900;
    const h = 500;

    svg.selectAll("*").remove();
    svg.append("text")
        .attr("x", w / 2).attr("y", h / 2)
        .attr("text-anchor", "middle")
        .attr("fill", "#444")
        .attr("font-size", "13px")
        .text("Loading gbrain graph...");

    let data;
    try {
        const r = await fetch(`http://${window.location.hostname}:8521/data/dna_graph.json`);
        data = await r.json();
    } catch (e) {
        svg.selectAll("*").remove();
        svg.append("text")
            .attr("x", w / 2).attr("y", h / 2)
            .attr("text-anchor", "middle")
            .attr("fill", "#ff4757")
            .attr("font-size", "13px")
            .text("gbrain offline — graph unavailable");
        return;
    }

    const { nodes, links, meta } = data;
    if (!nodes || nodes.length === 0) {
        svg.selectAll("*").remove();
        svg.append("text")
            .attr("x", w / 2).attr("y", h / 2)
            .attr("text-anchor", "middle")
            .attr("fill", "#444")
            .text("No nodes returned from gbrain.");
        return;
    }

    svg.selectAll("*").remove();

    // Meta label
    svg.append("text")
        .attr("x", 12).attr("y", 18)
        .attr("fill", "#444")
        .attr("font-size", "11px")
        .text(`${meta?.node_count ?? nodes.length} nodes · ${meta?.link_count ?? links.length} edges`);

    const sim = d3.forceSimulation(nodes)
        .force("link",   d3.forceLink(links).id(d => d.id).distance(d => d.type === "cluster" ? 80 : 40))
        .force("charge", d3.forceManyBody().strength(d => d.hub ? -400 : -60))
        .force("center", d3.forceCenter(w / 2, h / 2))
        .force("collision", d3.forceCollide(d => d.hub ? 18 : 8));

    const g = svg.append("g");

    // Zoom
    svg.call(d3.zoom().scaleExtent([0.2, 4]).on("zoom", e => g.attr("transform", e.transform)));

    // Links
    const link = g.append("g")
        .selectAll("line")
        .data(links)
        .join("line")
        .attr("stroke", d => d.status === "error" ? "#ff4757" : "#00ff9d")
        .attr("stroke-width", 0.8)
        .attr("stroke-opacity", 0.45);

    // Nodes
    const node = g.append("g")
        .selectAll("circle")
        .data(nodes)
        .join("circle")
        .attr("r", d => d.hub ? 12 : 5)
        .attr("fill", d => GROUP_COLORS[d.group] || GROUP_COLORS.concept)
        .attr("fill-opacity", d => d.hub ? 1 : 0.85)
        .attr("stroke", d => d.hub ? "#0a0a0a" : "none")
        .attr("stroke-width", d => d.hub ? 2 : 0)
        .style("cursor", "pointer")
        .call(d3.drag()
            .on("start", (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
            .on("drag",  (e, d) => { d.fx = e.x; d.fy = e.y; })
            .on("end",   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; })
        );

    // Hub labels
    const labels = g.append("g")
        .selectAll("text")
        .data(nodes.filter(d => d.hub))
        .join("text")
        .attr("text-anchor", "middle")
        .attr("dy", "2.2em")
        .attr("font-size", "9px")
        .attr("fill", d => GROUP_COLORS[d.group] || "#888")
        .attr("pointer-events", "none")
        .text(d => d.group.toUpperCase());

    // Tooltip
    const tip = d3.select("body").append("div")
        .style("position", "absolute")
        .style("background", "#111")
        .style("border", "1px solid #1e1e1e")
        .style("color", "#e0e0e0")
        .style("padding", "6px 10px")
        .style("border-radius", "6px")
        .style("font-size", "11px")
        .style("pointer-events", "none")
        .style("opacity", 0);

    node.on("mouseover", (e, d) => {
        tip.style("opacity", 1).html(`<strong>${d.id}</strong><br>${d.group}`);
    }).on("mousemove", e => {
        tip.style("left", (e.pageX + 12) + "px").style("top", (e.pageY - 20) + "px");
    }).on("mouseout", () => {
        tip.style("opacity", 0);
    });

    sim.on("tick", () => {
        link
            .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
            .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
        node
            .attr("cx", d => d.x)
            .attr("cy", d => d.y);
        labels
            .attr("x", d => d.x)
            .attr("y", d => d.y);
    });

    dnaLoaded = true;
}
