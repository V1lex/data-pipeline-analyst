import { ReactFlow, Background, Controls, MarkerType } from '@xyflow/react';
import '@xyflow/react/dist/style.css';

export default function PipelineGraph({ specification }) {
  const sources = specification?.sources || [];
  const steps = specification?.steps || [];
  const sinks = specification?.sinks || [];
  const known = new Set([...sources, ...steps].map(s => s.id).concat(sinks.map(s => `sink:${s.id}`)));
  const nodes = [
    ...sources.map((s, i) => ({ id: s.id, position: { x: 0, y: i * 130 }, data: { label: `${s.name || s.id}\n${s.format || 'источник'}` }, className: 'source-node' })),
    ...steps.map((s, i) => ({ id: s.id, position: { x: 240 + i * 210, y: 50 + (i % 2) * 50 }, data: { label: `${s.name || s.id}\n${s.operation?.type || 'операция'}` } })),
    ...sinks.map((s, i) => ({ id: `sink:${s.id}`, position: { x: 270 + steps.length * 210, y: i * 130 }, data: { label: `${s.name || s.id}\n${s.format || 'результат'}` }, className: 'sink-node' })),
  ];
  const edgeKeys = new Set();
  const edges = [];
  function add(source, target) {
    const id = `${source}->${target}`;
    if (!source || !known.has(source) || !known.has(target) || source === target || edgeKeys.has(id)) return;
    edgeKeys.add(id);
    edges.push({ id, source, target, markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#8a9ab5', strokeWidth: 1.5 } });
  }
  steps.forEach(s => {
    const op = s.operation || {};
    [...(s.inputs || []), ...(s.depends_on || []), op.source_id, op.right_input, ...(op.other_inputs || [])].forEach(input => add(input, s.id));
  });
  sinks.forEach(s => add(s.from_step, `sink:${s.id}`));
  if (!nodes.length) return <div className="graph-empty"><span>◇</span><strong>Здесь появится ваш конвейер</strong><p>Опишите источники, преобразования и ожидаемый результат в чате.</p></div>;
  return <ReactFlow key={JSON.stringify(specification)} nodes={nodes} edges={edges} fitView fitViewOptions={{ padding: 0.22 }} nodesDraggable={false} nodesConnectable={false} elementsSelectable={true} minZoom={0.15} maxZoom={1.8} attributionPosition="bottom-right"><Background gap={20} color="#dce3ed"/><Controls showInteractive={false}/></ReactFlow>;
}
