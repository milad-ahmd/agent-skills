export default function Project({ params }: { params: { id: string } }) {
  return <h1 data-testid="project-title">Project {params.id}</h1>;
}
