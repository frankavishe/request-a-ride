import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import './App.css'

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN

type RideStatus = 'requested' | 'accepted' | 'arriving' | 'in_progress' | 'completed' | 'cancelled'
type Profile = { id: string; name: string; email: string; role: 'rider' | 'driver'; available?: boolean }
type Place = { name: string; address: string; latitude: number; longitude: number }
type Ride = {
  id: string; pickup: Place; dropoff: Place; fare: number; distance_km: number
  duration_min: number; status: RideStatus; driver_name?: string; payment_status: string; created_at?: string
}

async function request<T>(path: string, token?: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(body.detail ?? 'Something went wrong. Please try again.')
  return body as T
}

function Icon({ name, size = 19 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    brand: <><path d="M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0Z" /><path d="M10 10h4m-2-2 2 2-2 2" /></>,
    pin: <><path d="M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0Z" /><circle cx="12" cy="10" r="2.5" /></>,
    swap: <><path d="M4 7h15l-4-4" /><path d="m20 17-15 0 4 4" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    car: <><path d="m5 11 1.5-5h11l1.5 5" /><path d="M3 11h18v7H3zM7 18v2m10-2v2" /><circle cx="7" cy="15" r=".8" /><circle cx="17" cy="15" r=".8" /></>,
    user: <><circle cx="12" cy="8" r="4" /><path d="M4 21v-2a8 8 0 0 1 16 0v2" /></>,
    arrow: <><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></>,
    logout: <><path d="M10 17l5-5-5-5" /><path d="M15 12H3" /><path d="M12 3h6a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-6" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
    steering: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="2" /><path d="m4 8 6 3m10-3-6 3m-2 3v7" /></>,
    wallet: <><rect x="3" y="5" width="18" height="15" rx="2" /><path d="M3 9h18m-5 5h2" /><path d="M5 5V4a2 2 0 0 1 2-2h10" /></>,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] ?? paths.brand}</svg>
}

function MapArt({ route = false, places }: { route?: boolean; places?: { pickup?: Place; dropoff?: Place } }) {
  const [mapFailed, setMapFailed] = useState(false)
  const mapUrl = MAPBOX_TOKEN && places?.pickup && places?.dropoff
    ? `https://api.mapbox.com/styles/v1/mapbox/dark-v11/static/pin-s+9dc45a(${places.pickup.longitude},${places.pickup.latitude}),pin-l+e69b66(${places.dropoff.longitude},${places.dropoff.latitude})/auto/1100x850@2x?access_token=${MAPBOX_TOKEN}`
    : ''
  return (
    <div className={`map-art${mapUrl && !mapFailed ? ' map-has-static' : ''}`} aria-label="Map preview">
      {mapUrl && !mapFailed && <img className="mapbox-static" src={mapUrl} alt={`Map from ${places?.pickup?.name} to ${places?.dropoff?.name}`} onError={() => setMapFailed(true)} />}
      <div className="map-grid" />
      <svg className="map-lines" viewBox="0 0 760 580" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <path d="M-20 92 185 222l79-40 179 101 96-60 250 155M-15 510l183-146 104 37 135-105 111 33L780 164M72-15l79 201-28 98 117 112 14 225M333-10l-37 132 89 140-34 118 79 144M634-18l-55 178 64 114-82 128 31 174M-22 248l170-53 151 42 163-92 145 19 179-89" />
        <path className="street-minor" d="m15 156 152 72 90 131 135-12 106 69 166-29M254-16l82 122 164 18 82 93 122 35M-24 415l139 30 66-49-16-131 96-101M456 590l-46-151 79-79 139-42" />
        {route && <path className="route-path" d="M156 382c59-37 68-120 158-118s71 92 157 63 102-115 173-133" />}
      </svg>
      <div className="map-label label-one">WEST END</div><div className="map-label label-two">CITY CENTRE</div><div className="map-label label-three">RIVERSIDE</div>
      {route && <><span className="map-pin pickup-pin"><Icon name="pin" size={22} /></span><span className="map-pin drop-pin"><Icon name="pin" size={22} /></span><span className="map-car"><Icon name="car" size={17} /></span></>}
      <span className="map-attribution">{mapUrl && !mapFailed ? '© Mapbox © OpenStreetMap' : 'Mapbox · OpenStreetMap'}</span>
    </div>
  )
}

function AuthScreen({ onLogin }: { onLogin: (token: string, profile: Profile) => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [role, setRole] = useState<'rider' | 'driver'>('rider')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true); setError('')
    try {
      const result = mode === 'register'
        ? await request<{ access_token: string; user: Profile }>('/api/auth/register', undefined, { method: 'POST', body: JSON.stringify({ name, email, password, role }) })
        : await request<{ access_token: string; user: Profile }>('/api/auth/login', undefined, { method: 'POST', body: JSON.stringify({ email, password }) })
      onLogin(result.access_token, result.user)
    } catch (issue) { setError(issue instanceof Error ? issue.message : 'Unable to sign in.') }
    finally { setBusy(false) }
  }

  return (
    <main className="auth-layout">
      <section className="auth-art">
        <a className="wordmark" href="#"><Icon name="brand" size={23} /> waygo<span>.</span></a>
        <div className="auth-message"><span className="eyebrow"><i /> YOUR CITY, IN MOTION</span><h1>Good things<br />are <em>closer.</em></h1><p>Your ride is just around the corner.<br />Get there your way.</p></div>
        <MapArt route />
        <div className="auth-foot"><span>Move at your own pace.</span><span>01 / 03</span></div>
      </section>
      <section className="auth-panel">
        <div className="auth-mobile-logo"><a className="wordmark" href="#"><Icon name="brand" size={21} /> waygo<span>.</span></a></div>
        <form className="auth-form" onSubmit={submit}>
          <span className="eyebrow muted">{mode === 'login' ? 'WELCOME BACK' : 'FIRST THINGS FIRST'}</span>
          <h2>{mode === 'login' ? 'Let’s get you there.' : 'Your next ride starts here.'}</h2>
          <p className="auth-subtitle">{mode === 'login' ? 'Sign in to pick up where you left off.' : 'Create an account and get moving.'}</p>
          {mode === 'register' && <><label>Your name<input value={name} onChange={e => setName(e.target.value)} autoComplete="name" placeholder="Alex Morgan" required /></label><fieldset className="role-select"><legend>I’m joining as</legend><button type="button" className={role === 'rider' ? 'role-choice selected' : 'role-choice'} onClick={() => setRole('rider')}><Icon name="user" /> Rider</button><button type="button" className={role === 'driver' ? 'role-choice selected' : 'role-choice'} onClick={() => setRole('driver')}><Icon name="steering" /> Driver</button></fieldset></>}
          <label>Email address<input type="email" value={email} onChange={e => setEmail(e.target.value)} autoComplete="email" placeholder="you@example.com" required /></label>
          <label>Password<input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} placeholder="At least 8 characters" minLength={8} required /></label>
          {error && <p className="form-error">{error}</p>}
          <button className="button button-primary auth-submit" disabled={busy}>{busy ? 'One moment…' : mode === 'login' ? 'Sign in' : 'Create account'}<Icon name="arrow" /></button>
          <p className="auth-switch">{mode === 'login' ? 'New around here?' : 'Already have an account?'} <button type="button" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError('') }}>{mode === 'login' ? 'Create an account' : 'Sign in'}</button></p>
        </form>
        <p className="auth-legal">By continuing, you agree to our <a href="#terms">Terms</a> and <a href="#privacy">Privacy Policy</a>.</p>
      </section>
    </main>
  )
}

function App() {
  const [token, setToken] = useState(() => localStorage.getItem('waygo_token') ?? '')
  const [profile, setProfile] = useState<Profile | null>(null)
  const [pickup, setPickup] = useState('')
  const [dropoff, setDropoff] = useState('')
  const [ride, setRide] = useState<Ride | null>(null)
  const [estimate, setEstimate] = useState<{ distance_km: number; duration_min: number; fare: number } | null>(null)
  const [places, setPlaces] = useState<{ pickup?: Place; dropoff?: Place }>({})
  const [suggestions, setSuggestions] = useState<{ field: 'pickup' | 'dropoff'; options: Place[] } | null>(null)
  const [openRides, setOpenRides] = useState<Ride[]>([])
  const [history, setHistory] = useState<Ride[]>([])
  const [historyBusy, setHistoryBusy] = useState(false)
  const [activeTab, setActiveTab] = useState<'ride' | 'activity'>('ride')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [paymentUrl, setPaymentUrl] = useState('')
  const checkoutAttempted = useRef(new Set<string>())
  const searchTimer = useRef<number | null>(null)
  const searchVersion = useRef(0)

  function logout() {
    localStorage.removeItem('waygo_token'); setToken(''); setProfile(null); setRide(null)
  }

  useEffect(() => {
    if (!token) { setProfile(null); return }
    request<Profile>('/api/auth/me', token).then(setProfile).catch(logout)
  }, [token])

  useEffect(() => {
    if (!profile || profile.role !== 'driver') return
    const load = () => request<Ride[]>('/api/rides/open', token).then(setOpenRides).catch(error => setNotice(error.message))
    load()
    const interval = window.setInterval(load, 15000)
    return () => window.clearInterval(interval)
  }, [profile, token])

  useEffect(() => {
    if (!profile || profile.role !== 'rider') return
    let active = true
    const syncRide = async () => {
      try {
        const activeRide = await request<Ride | null>('/api/rides/active', token)
        if (active) setRide(activeRide)
      } catch (error) {
        if (active) setNotice(error instanceof Error ? error.message : 'Could not refresh your ride.')
      }
    }
    syncRide()
    const interval = window.setInterval(syncRide, 5000)
    return () => { active = false; window.clearInterval(interval) }
  }, [profile, token])

  useEffect(() => {
    if (!profile || profile.role !== 'rider' || !ride || !['accepted', 'arriving', 'in_progress', 'completed'].includes(ride.status) || ride.payment_status === 'paid' || checkoutAttempted.current.has(ride.id)) return
    checkoutAttempted.current.add(ride.id)
    request<{ url: string }>(`/api/rides/${ride.id}/checkout`, token, { method: 'POST' })
      .then(result => setPaymentUrl(result.url))
      .catch(error => setNotice(error instanceof Error ? error.message : 'Could not start payment checkout.'))
  }, [profile, ride, token])

  useEffect(() => {
    if (!notice) return
    const id = window.setTimeout(() => setNotice(''), 5000)
    return () => window.clearTimeout(id)
  }, [notice])

  function login(accessToken: string, user: Profile) {
    localStorage.setItem('waygo_token', accessToken); setToken(accessToken); setProfile(user)
  }

  async function searchPlaces(field: 'pickup' | 'dropoff', value: string) {
    if (field === 'pickup') setPickup(value); else setDropoff(value)
    setPlaces(current => ({ ...current, [field]: undefined }))
    setEstimate(null)
    const version = ++searchVersion.current
    if (searchTimer.current !== null) window.clearTimeout(searchTimer.current)
    if (value.trim().length < 3) { setSuggestions(null); return }
    if (!MAPBOX_TOKEN) { setSuggestions(null); return }
    searchTimer.current = window.setTimeout(async () => {
      try {
        const results = await request<{ places: Place[] }>(`/api/maps/geocode?q=${encodeURIComponent(value)}`, token)
        if (version === searchVersion.current) setSuggestions({ field, options: results.places })
      } catch (error) {
        if (version === searchVersion.current) setNotice(error instanceof Error ? error.message : 'Location search failed.')
      }
    }, 300)
  }

  async function getEstimate() {
    if (!places.pickup || !places.dropoff) { setNotice('Choose pickup and destination from the location results first.'); return }
    setBusy(true); setNotice('')
    try {
      const result = await request<{ distance_km: number; duration_min: number; fare: number }>('/api/maps/route', token, { method: 'POST', body: JSON.stringify({ pickup: places.pickup, dropoff: places.dropoff }) })
      setEstimate(result)
    } catch (error) { setNotice(error instanceof Error ? error.message : 'Could not get a route.') }
    finally { setBusy(false) }
  }

  function choosePlace(field: 'pickup' | 'dropoff', place: Place) {
    searchVersion.current++
    if (searchTimer.current !== null) window.clearTimeout(searchTimer.current)
    if (field === 'pickup') setPickup(place.name); else setDropoff(place.name)
    setPlaces(current => ({ ...current, [field]: place }))
    setSuggestions(null)
    setEstimate(null)
  }

  function locatePickup() {
    if (!navigator.geolocation) { setNotice('Location access is not available in this browser.'); return }
    navigator.geolocation.getCurrentPosition(async position => {
      try {
        const params = new URLSearchParams({ longitude: String(position.coords.longitude), latitude: String(position.coords.latitude) })
        const place = await request<Place>(`/api/maps/reverse?${params}`, token)
        choosePlace('pickup', place)
        setNotice('Pickup set to your current location.')
      } catch (error) { setNotice(error instanceof Error ? error.message : 'Could not look up your location.') }
    }, error => setNotice(error.code === error.PERMISSION_DENIED ? 'Allow location access in your browser to use your current location.' : 'Your current location is not available.'), { enableHighAccuracy: false, timeout: 10000 })
  }

  async function bookRide() {
    if (!places.pickup || !places.dropoff) return
    setBusy(true); setNotice('')
    try {
      const result = await request<{ ride: Ride; checkout_url?: string }>('/api/rides', token, { method: 'POST', body: JSON.stringify({ pickup: places.pickup, dropoff: places.dropoff }) })
      setRide(result.ride)
      setNotice('Ride requested. You’ll be able to pay securely after a driver accepts.')
    } catch (error) { setNotice(error instanceof Error ? error.message : 'Ride request failed.') }
    finally { setBusy(false) }
  }

  async function cancelRide(id: string) {
    setBusy(true); setNotice('')
    try {
      await request(`/api/rides/${id}/cancel`, token, { method: 'POST' })
      setRide(null); setPaymentUrl('')
      setNotice('Your unassigned ride request was cancelled.')
    } catch (error) { setNotice(error instanceof Error ? error.message : 'Could not cancel the ride request.') }
    finally { setBusy(false) }
  }

  async function driverAction(path: string, method: string, body?: object) {
    setBusy(true); setNotice('')
    try {
      const result = await request<Ride>(path, token, { method, ...(body ? { body: JSON.stringify(body) } : {}) })
      setRide(result)
      setOpenRides(current => result.status === 'completed' || result.status === 'cancelled'
        ? current.filter(item => item.id !== result.id)
        : [result, ...current.filter(item => item.id !== result.id)])
    } catch (error) { setNotice(error instanceof Error ? error.message : 'Could not update the ride.') }
    finally { setBusy(false) }
  }

  async function showActivity() {
    setActiveTab('activity')
    setHistoryBusy(true)
    try { setHistory(await request<Ride[]>('/api/rides/history', token)) }
    catch (error) { setNotice(error instanceof Error ? error.message : 'Could not load ride activity.') }
    finally { setHistoryBusy(false) }
  }

  if (!profile) return <AuthScreen onLogin={login} />

  const isDriver = profile.role === 'driver'
  const displayedRide = ride ?? (activeTab === 'activity' ? history[0] : null)
  return (
    <main className="app-shell">
      <header className="topbar"><a className="wordmark dark-wordmark" href="#"><Icon name="brand" size={22} /> waygo<span>.</span></a><nav className="topnav"><button className={activeTab === 'ride' ? 'nav-active' : ''} onClick={() => setActiveTab('ride')}>{isDriver ? 'Drive' : 'Ride'}</button><button className={activeTab === 'activity' ? 'nav-active' : ''} onClick={showActivity}>Activity</button></nav><div className="top-user"><span className="avatar">{profile.name.charAt(0).toUpperCase()}</span><span className="user-name">{profile.name.split(' ')[0]}</span><button className="icon-button logout-button" onClick={logout} aria-label="Sign out"><Icon name="logout" /></button></div></header>
      <div className="dashboard">
        {activeTab === 'ride' ? <section className="booking-panel" id="ride">
          <span className="eyebrow muted">{isDriver ? 'DRIVER SPACE' : 'THE CITY IS YOURS'}</span>
          <h1>{isDriver ? <>Your next ride<br /><em>starts here.</em></> : <>Where to,<br /><em>{profile.name.split(' ')[0]}?</em></>}</h1>
          <p className="section-subtitle">{isDriver ? 'Good things happen when you get moving.' : 'The best routes start with you.'}</p>
          {isDriver ? <DriverPanel rides={openRides} busy={busy} onToggle={async () => { try { const updated = await request<Profile>('/api/drivers/availability', token, { method: 'PATCH', body: JSON.stringify({ available: !profile.available }) }); setProfile(updated); setNotice(updated.available ? 'You’re online and ready for trips.' : 'You’re now offline.') } catch (error) { setNotice(error instanceof Error ? error.message : 'Could not update availability.') } }} onAccept={id => driverAction(`/api/rides/${id}/accept`, 'POST')} onStatus={(id, status) => driverAction(`/api/rides/${id}/status`, 'PATCH', { status })} available={profile.available ?? false} /> : <RiderPanel pickup={pickup} dropoff={dropoff} estimate={estimate} ride={ride} places={places} suggestions={suggestions} paymentUrl={paymentUrl} busy={busy} onInput={searchPlaces} onChoose={choosePlace} onSwap={() => { setPickup(dropoff); setDropoff(pickup); setPlaces({ pickup: places.dropoff, dropoff: places.pickup }); setEstimate(null) }} onEstimate={getEstimate} onBook={bookRide} onCancel={cancelRide} onReset={() => { setRide(null); setPaymentUrl(''); setEstimate(null) }} /> }
          <div className="trust-row"><span><Icon name="check" size={15} /> Upfront pricing</span><span><Icon name="check" size={15} /> Safe by design</span></div>
        </section> : <ActivityPanel rides={history} loading={historyBusy} />}
        <section className="map-panel"><MapArt route={Boolean(estimate || displayedRide)} places={displayedRide ? { pickup: displayedRide.pickup, dropoff: displayedRide.dropoff } : places} />{estimate && !displayedRide && <div className="estimate-float"><span><Icon name="clock" size={16} /> {estimate.duration_min} min</span><span>{estimate.distance_km} km</span></div>}{displayedRide && <div className="ride-float"><span className="live-dot" /> TRIP {displayedRide.status.replace('_', ' ').toUpperCase()}<strong>{displayedRide.driver_name ?? 'Finding a driver'}</strong>{displayedRide.payment_status && <small>Payment · {displayedRide.payment_status}</small>}</div>}<div className="map-controls"><button className="map-control" onClick={() => { setPlaces({}); setPickup(''); setDropoff(''); setEstimate(null); setRide(null); setPaymentUrl('') }} aria-label="Reset map"><Icon name="search" /></button><button className="map-control" onClick={locatePickup} aria-label="Use my current location"><Icon name="pin" /></button></div></section>
      </div>
      <footer className="app-footer"><span>Move freely. <span className="footer-accent">Go further.</span></span><span>© 2026 waygo. &nbsp;·&nbsp; Built for the way you move.</span></footer>
      {notice && <div className="toast" role="status">{notice}<button onClick={() => setNotice('')} aria-label="Dismiss">×</button></div>}
    </main>
  )
}

function ActivityPanel({ rides, loading }: { rides: Ride[]; loading: boolean }) {
  return <section className="booking-panel activity-panel" id="activity">
    <span className="eyebrow muted">YOUR JOURNEY</span>
    <h1>Your ride<br /><em>history.</em></h1>
    <p className="section-subtitle">Every trip, all in one place.</p>
    <div className="activity-list">{loading ? <p className="activity-empty">Loading your trips…</p> : rides.length ? rides.map(item => <article className="activity-item" key={item.id}><div className="activity-route"><span className={`activity-status status-${item.status}`}><Icon name={item.status === 'completed' ? 'check' : 'car'} size={15} /></span><span><strong>{item.pickup.name}</strong><small>{item.dropoff.name}</small></span><b>£{item.fare.toFixed(2)}</b></div><div className="activity-meta"><span>{item.status.replace('_', ' ')}</span><span>{item.created_at ? new Date(item.created_at).toLocaleDateString() : 'Recent trip'}</span></div></article>) : <div className="activity-empty"><span><Icon name="clock" size={21} /></span><strong>No trips yet</strong><p>Your ride history will show up here.</p></div>}</div>
  </section>
}

function RiderPanel({ pickup, dropoff, estimate, ride, places, suggestions, paymentUrl, busy, onInput, onChoose, onSwap, onEstimate, onBook, onCancel, onReset }: {
  pickup: string; dropoff: string; estimate: { distance_km: number; duration_min: number; fare: number } | null
  ride: Ride | null; places: { pickup?: Place; dropoff?: Place }; suggestions: { field: 'pickup' | 'dropoff'; options: Place[] } | null
  paymentUrl: string; busy: boolean; onInput: (field: 'pickup' | 'dropoff', value: string) => void
  onChoose: (field: 'pickup' | 'dropoff', place: Place) => void; onSwap: () => void
  onEstimate: () => void; onBook: () => void; onCancel: (id: string) => void; onReset: () => void
}) {
  if (ride) return <div className="trip-card"><div className="trip-heading"><span className="live-dot" /><span>YOUR RIDE</span><b>{ride.status.replace('_', ' ')}</b></div><div className="trip-route"><div className="route-dots"><i /><span /><i /></div><div><strong>{ride.pickup.name}</strong><strong>{ride.dropoff.name}</strong></div></div><div className="trip-driver"><span className="driver-avatar"><Icon name="user" /></span><div><b>{ride.driver_name ?? 'Finding a driver'}</b><span>{ride.payment_status === 'paid' ? 'Payment complete' : ride.driver_name ? 'Your driver is on the way' : 'We’re matching you with a nearby driver'}</span></div><strong>£{ride.fare.toFixed(2)}</strong></div>{paymentUrl && ride.payment_status !== 'paid' && <a className="button button-primary checkout-button" href={paymentUrl}>Continue to secure payment <Icon name="arrow" /></a>}{ride.status === 'requested' ? <button className="text-button trip-done" disabled={busy} onClick={() => onCancel(ride.id)}>Cancel ride request</button> : ride.status === 'completed' && ride.payment_status === 'paid' ? <button className="text-button trip-done" onClick={onReset}>Request another ride</button> : <p className="trip-waiting">{ride.status === 'completed' ? 'Complete your payment to finish this trip.' : 'You can request another ride after this trip is complete.'}</p>}</div>
  return <div className="ride-form">
    <div className="location-inputs"><div className="input-icons"><span className="origin-dot" /><span className="connector" /><span className="destination-pin"><Icon name="pin" size={17} /></span></div><div className="place-fields"><div className="place-wrap"><input aria-label="Pickup location" placeholder="Pickup location" value={pickup} onChange={event => onInput('pickup', event.target.value)} autoComplete="off" />{suggestions?.field === 'pickup' && <PlaceSuggestions options={suggestions.options} onChoose={place => onChoose('pickup', place)} />}</div><div className="input-divider" /><div className="place-wrap"><input aria-label="Where to" placeholder="Where to?" value={dropoff} onChange={event => onInput('dropoff', event.target.value)} autoComplete="off" />{suggestions?.field === 'dropoff' && <PlaceSuggestions options={suggestions.options} onChoose={place => onChoose('dropoff', place)} />}</div></div><button className="swap-button" onClick={onSwap} aria-label="Swap locations"><Icon name="swap" /></button></div>
    {!MAPBOX_TOKEN && <div className="provider-note"><span>i</span><span>Configure <code>VITE_MAPBOX_TOKEN</code> and backend <code>MAPBOX_ACCESS_TOKEN</code> to search and map places.</span></div>}
    {estimate ? <div className="fare-card"><div><span>ESTIMATED FARE</span><strong>£{estimate.fare.toFixed(2)}</strong></div><div className="fare-meta"><span><Icon name="clock" size={15} /> {estimate.duration_min} min</span><span><Icon name="car" size={16} /> {estimate.distance_km} km</span></div><button className="button button-primary" onClick={onBook} disabled={busy}>{busy ? 'Requesting…' : 'Request a ride'}<Icon name="arrow" /></button></div> : <button className="button button-primary estimate-button" onClick={onEstimate} disabled={busy}>{busy ? 'Finding a route…' : 'See ride options'}<Icon name="arrow" /></button>}
    {!estimate && <div className="pickup-caption"><Icon name="pin" size={15} /> Add your start and destination to see ride options</div>}
    <div className="ride-option"><div className="ride-icon"><Icon name="car" size={23} /></div><div className="ride-details"><strong>Waygo</strong><span>Everyday rides <i /> 3 min away</span></div><strong className="ride-price">{estimate ? `£${estimate.fare.toFixed(2)}` : '—'}</strong></div>
    {paymentUrl && <a className="button button-primary checkout-button" href={paymentUrl}>Pay securely <Icon name="wallet" /></a>}
  </div>
}

function PlaceSuggestions({ options, onChoose }: { options: Place[]; onChoose: (place: Place) => void }) {
  if (!options.length) return <div className="suggestion-list no-matches" role="status">No matching UK locations were found.</div>
  return <div className="suggestion-list" role="listbox">{options.map((place, index) => <button key={`${place.latitude}-${place.longitude}-${index}`} role="option" aria-selected="false" onClick={() => onChoose(place)}><Icon name="pin" size={17} /><span><b>{place.name}</b><small>{place.address}</small></span></button>)}</div>
}

function DriverPanel({ rides, busy, available, onToggle, onAccept, onStatus }: {
  rides: Ride[]; busy: boolean; available: boolean; onToggle: () => void
  onAccept: (id: string) => void; onStatus: (id: string, status: RideStatus) => void
}) {
  return <div className="driver-panel"><button className={`availability-button${available ? ' is-online' : ''}`} onClick={onToggle} disabled={busy}><span className="availability-dot" />{available ? 'You’re online' : 'Go online'}<span className="availability-switch" /></button><p className="driver-caption">{available ? 'You can receive trip requests nearby.' : 'Go online when you’re ready to drive.'}</p><div className="driver-rides-title"><span>Your trips</span><span>{rides.length} nearby</span></div>{rides.length ? <div className="driver-rides">{rides.map(item => <article className="driver-ride" key={item.id}><div className="trip-route"><div className="route-dots"><i /><span /><i /></div><div><strong>{item.pickup.name}</strong><strong>{item.dropoff.name}</strong></div></div><div className="driver-trip-meta"><span><Icon name="clock" size={14} /> {item.duration_min} min · {item.status.replace('_', ' ')}</span><b>£{item.fare.toFixed(2)}</b></div>{item.status === 'requested' ? <button className="button button-primary driver-accept" disabled={!available || busy} onClick={() => onAccept(item.id)}>Accept trip<Icon name="arrow" /></button> : item.status !== 'completed' && item.status !== 'cancelled' ? <button className="button button-primary driver-accept" disabled={busy} onClick={() => onStatus(item.id, item.status === 'accepted' ? 'arriving' : item.status === 'arriving' ? 'in_progress' : 'completed')}>{item.status === 'accepted' ? 'Heading to pickup' : item.status === 'arriving' ? 'Start the ride' : 'Complete ride'}<Icon name="arrow" /></button> : <span className="driver-caption">Trip complete</span>}</article>)}</div> : <div className="empty-rides"><span><Icon name="steering" size={21} /></span><strong>No trips just yet</strong><p>New requests will show up here while you’re online.</p></div>}</div>
}

export default App
