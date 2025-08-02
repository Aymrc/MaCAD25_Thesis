// Load and render the graph
fetch("../3_enrichedgraph/enriched_graph.json")
  .then(res => res.json())
  .then(data => {
    const Graph = ForceGraph3D()
      (document.getElementById('3d-graph'))
      .graphData(data)
      .nodeAutoColorBy('assigned_programs')
      .nodeLabel(node => {
        if (node.assigned_programs && node.assigned_programs.length > 0) {
          return node.assigned_programs
            .map(p => `${p[0]} (${p[1]})`)
            .join(', ');
        }
        return `Node ${node.id}`;
      })
      .linkDirectionalParticles(2)
      .linkDirectionalParticleSpeed(0.005)
      .backgroundColor("#000");
  });


// Toggle chat bar expansion
function toggleChat() {
  const chat = document.getElementById('chatBar');
  const input = document.getElementById('chatInput');
  const label = document.getElementById('chatLabel');
  chat.classList.toggle('open');
  if (chat.classList.contains('open')) {
    input.style.display = 'block';
    label.style.display = 'none';
    input.focus();
  } else {
    input.style.display = 'none';
    label.style.display = 'block';
  }
}
