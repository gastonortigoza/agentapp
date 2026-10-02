import { useState, useEffect, useCallback, useRef } from 'react';
import { Card, groups, filters } from './directory';
import PublicProfile from './PublicProfile';

interface GeographyData {
  countries: { id: string; name: string; code: string }[];
  provinces: { id: string; name: string; country_id: string }[];
  zones: { id: string; name: string; country_id: string; province_id: string }[];
}

interface ProfileResponse {
  items: Card[];
  next_cursor: string | null;
}

interface ApiError {
  error: { code: string; message: string };
}

function Directory() {
  const [geography, setGeography] = useState<GeographyData | null>(null);
  const [profiles, setProfiles] = useState<Card[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  const [countryId, setCountryId] = useState('');
  const [provinceId, setProvinceId] = useState('');
  const [zoneId, setZoneId] = useState('');
  
  const abortControllerRef = useRef<AbortController | null>(null);

  const fetchGeography = useCallback(async () => {
    try {
      const res = await fetch('/api/catalog/geography');
      if (!res.ok) throw new Error('Failed to fetch geography');
      const data: GeographyData = await res.json();
      setGeography(data);
    } catch (e) {
      setError('Error loading geography data');
    }
  }, []);

  const fetchProfiles = useCallback(async (cursor: string | null = null) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    
    const controller = new AbortController();
    abortControllerRef.current = controller;

    const params = filters(countryId, provinceId, zoneId);
    if (cursor) {
      params.append('cursor', cursor);
    }

    try {
      const res = await fetch(`/api/profiles?${params.toString()}`, {
        signal: controller.signal
      });
      
      if (!res.ok) {
        const errData: ApiError = await res.json();
        throw new Error(errData.error.message || 'Failed to fetch profiles');
      }

      const data: ProfileResponse = await res.json();
      
      if (cursor) {
        setProfiles(prev => [...prev, ...data.items]);
      } else {
        setProfiles(data.items);
      }
      
      setNextCursor(data.next_cursor);
      setLoading(false);
      setError(null);
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        setError(e.message || 'Failed to load profiles');
        setLoading(false);
      }
    }
  }, [countryId, provinceId, zoneId]);

  useEffect(() => {
    fetchGeography();
  }, [fetchGeography]);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setNextCursor(null);
    fetchProfiles(null);
    
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, [fetchProfiles]);

  const handleCountryChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setCountryId(e.target.value);
    setProvinceId('');
    setZoneId('');
  };

  const handleProvinceChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setProvinceId(e.target.value);
    setZoneId('');
  };

  const handleZoneChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setZoneId(e.target.value);
  };

  const handleRetry = () => {
    setLoading(true);
    setError(null);
    setNextCursor(null);
    fetchProfiles(null);
  };

  const handleLoadMore = () => {
    if (nextCursor) {
      fetchProfiles(nextCursor);
    }
  };

  const filteredProvinces = geography?.provinces.filter(p => p.country_id === countryId) || [];
  const filteredZones = geography?.zones.filter(z => z.province_id === provinceId) || [];

  const { promoted, basic } = groups(profiles);

  const renderCard = (card: Card) => (
    <li key={card.id} className="card">
      <img src={card.main_photo.url} alt={card.display_name} />
      <h3><a href={`/personas/${encodeURIComponent(card.id)}`}>{card.display_name}</a></h3>
      <p>{card.description}</p>
      <a href={card.whatsapp_url} target="_blank" rel="noopener noreferrer">Contact</a>
    </li>
  );

  return (
    <div className="app-container">
      <header>
        <h1>Directory</h1>
        <a href="/registro" className="register-link">Registrarse</a>
      </header>
      
      <div className="filters">
        <label>
          País
          <select aria-label="País" value={countryId} onChange={handleCountryChange}>
            <option value="">Todos</option>
            {geography?.countries.map(c => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </label>
        
        <label>
          Provincia
          <select aria-label="Provincia" value={provinceId} onChange={handleProvinceChange} disabled={!countryId}>
            <option value="">Todas</option>
            {filteredProvinces.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>
        
        <label>
          Zona
          <select aria-label="Zona" value={zoneId} onChange={handleZoneChange} disabled={!provinceId}>
            <option value="">Todas</option>
            {filteredZones.map(z => (
              <option key={z.id} value={z.id}>{z.name}</option>
            ))}
          </select>
        </label>
      </div>

      <main>
        {loading && profiles.length === 0 && (
          <div className="loading-state">Cargando...</div>
        )}

        {error && (
          <div className="error-state">
            <p>{error}</p>
            <button onClick={handleRetry}>Reintentar</button>
          </div>
        )}

        {!loading && !error && profiles.length === 0 && (
          <div className="empty-state">
            <p>Sin coincidencias.</p>
          </div>
        )}

        {!loading && !error && profiles.length > 0 && (
          <>
            {promoted.length > 0 && (
              <section aria-label="Promocionados">
                <h2>Promocionados</h2>
                <ul className="card-grid">
                  {promoted.map(renderCard)}
                </ul>
              </section>
            )}

            {basic.length > 0 && (
              <section aria-label="Listado básico">
                <h2>Listado básico</h2>
                <ul className="card-grid">
                  {basic.map(renderCard)}
                </ul>
              </section>
            )}
          </>
        )}
      </main>

      {nextCursor && !loading && !error && (
        <div className="load-more">
          <button onClick={handleLoadMore} disabled={loading}>
            {loading ? 'Cargando...' : 'Cargar más'}
          </button>
        </div>
      )}
    </div>
  );
}

export default function App() {
  const match = /^\/personas\/([^/]+)\/?$/.exec(window.location.pathname);
  if (match) return <PublicProfile profileId={decodeURIComponent(match[1])} />;
  return <Directory />;
}
