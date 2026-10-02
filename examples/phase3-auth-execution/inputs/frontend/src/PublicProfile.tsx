import { useEffect, useState, useCallback } from 'react';

interface Photo {
  id: string;
  url: string;
  is_main: boolean;
}

interface Profile {
  id: string;
  display_name: string;
  age: number;
  gender: string;
  zone_label: string;
  description: string;
  whatsapp_url: string;
  photos: Photo[];
}

export default function PublicProfile({ profileId }: { profileId: string }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [is404, setIs404] = useState(false);

  const fetchProfile = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    setIs404(false);
    
    try {
      const response = await fetch(`/api/profiles/${encodeURIComponent(profileId)}`, {
        signal,
      });

      if (!response.ok) {
        if (response.status === 404) {
          setIs404(true);
          setError('Perfil no disponible');
        } else {
          setError('Error al cargar el perfil');
        }
        return;
      }

      const data = await response.json();
      if (data && data.profile) {
        setProfile(data.profile);
      } else {
        setError('Error al cargar el perfil');
      }
    } catch (err: any) {
      if (err.name === 'AbortError') {
        return;
      }
      setError('Error al cargar el perfil');
    } finally {
      if (!signal?.aborted) {
        setLoading(false);
      }
    }
  }, [profileId]);

  useEffect(() => {
    const controller = new AbortController();
    fetchProfile(controller.signal);
    
    return () => {
      controller.abort();
    };
  }, [fetchProfile]);

  const handleRetry = () => {
    fetchProfile();
  };

  // Sort photos: main first, then rest in returned order
  const sortedPhotos = profile?.photos ? [...profile.photos].sort((a, b) => {
    if (a.is_main && !b.is_main) return -1;
    if (!a.is_main && b.is_main) return 1;
    return 0;
  }) : [];

  return (
    <main style={{ maxWidth: '1280px', margin: '0 auto', padding: '1rem', boxSizing: 'border-box', overflow: 'hidden' }}>
      <nav style={{ marginBottom: '1rem' }}>
        <a href="/" style={{ color: '#007bff', textDecoration: 'none' }}>
          Volver al directorio
        </a>
      </nav>

      {loading && (
        <div role="status" aria-live="polite" style={{ padding: '2rem', textAlign: 'center' }}>
          Cargando perfil...
        </div>
      )}

      {!loading && error && (
        <div role="alert" aria-live="assertive" style={{ padding: '2rem', textAlign: 'center' }}>
          <p style={{ fontSize: '1.2rem', marginBottom: '1rem' }}>{error}</p>
          <button 
            onClick={handleRetry} 
            style={{ 
              padding: '0.5rem 1rem', 
              backgroundColor: '#007bff', 
              color: 'white', 
              border: 'none', 
              borderRadius: '4px', 
              cursor: 'pointer' 
            }}
          >
            Reintentar
          </button>
        </div>
      )}

      {!loading && !error && profile && (
        <article style={{ maxWidth: '600px', margin: '0 auto' }}>
          <h1 style={{ fontSize: '1.8rem', marginBottom: '0.5rem' }}>
            {profile.display_name}
          </h1>
          
          <div style={{ marginBottom: '1rem', color: '#555' }}>
            <p>{profile.age} años</p>
            <p>{profile.gender}</p>
            <p>{profile.zone_label}</p>
          </div>

          <p style={{ marginBottom: '1.5rem', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
            {profile.description}
          </p>

          <section aria-label="Galería de fotos" style={{ marginBottom: '1.5rem' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
              {sortedPhotos.map((photo, index) => (
                <img
                  key={photo.id}
                  src={photo.url}
                  alt={`Foto ${index + 1} de ${profile.display_name}${photo.is_main ? ' (principal)' : ''}`}
                  style={{ 
                    width: '100px', 
                    height: '100px', 
                    objectFit: 'cover', 
                    borderRadius: '8px', 
                    border: photo.is_main ? '2px solid #007bff' : '1px solid #ddd' 
                  }}
                />
              ))}
            </div>
          </section>

          {profile.whatsapp_url && (
            <a
              href={profile.whatsapp_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ 
                display: 'inline-block', 
                padding: '0.75rem 1.5rem', 
                backgroundColor: '#25D366', 
                color: 'white', 
                textDecoration: 'none', 
                borderRadius: '8px', 
                fontWeight: 'bold' 
              }}
            >
              Contactar por WhatsApp
            </a>
          )}
        </article>
      )}
    </main>
  );
}
