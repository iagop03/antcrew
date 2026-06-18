import { useEffect, useState } from 'react'
import RunList from './components/RunList'
import NewRunForm from './components/NewRunForm'
import RunDetail from './components/RunDetail'
import EvalList from './components/EvalList'
import NewEvalForm from './components/NewEvalForm'

function getRoute(): string {
  return window.location.hash.slice(1) || '/'
}

export default function App() {
  const [route, setRoute] = useState(getRoute)

  useEffect(() => {
    const handler = () => setRoute(getRoute())
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [])

  if (route.startsWith('/runs/')) return <RunDetail runId={route.slice(6)} />
  if (route === '/new')          return <NewRunForm />
  if (route === '/evals')        return <EvalList />
  if (route === '/evals/new')    return <NewEvalForm />
  return <RunList />
}
