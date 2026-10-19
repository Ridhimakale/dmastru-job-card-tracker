import { useEffect, useState } from 'react';
import { getCities, getStatesByShort } from 'countrycitystatejson/client';

type CityOption = { city: string; state: string; label: string };
let indiaCitiesPromise: Promise<CityOption[]> | undefined;
let nextListId = 0;

function loadIndiaCities() {
  if (!indiaCitiesPromise) {
    indiaCitiesPromise = (async () => {
      const states = getStatesByShort('IN') ?? [];
      const cityLists = await Promise.all(states.map(async state => ({
        state,
        cities: await getCities('IN', state) ?? [],
      })));
      return cityLists.flatMap(({ state, cities }) => cities.map(city => ({ city, state, label: `${city}, ${state}` })))
        .sort((a, b) => a.label.localeCompare(b.label));
    })();
  }
  return indiaCitiesPromise;
}

function findCity(value: string, cities: CityOption[], state?: string) {
  const normalized = value.trim().toLocaleLowerCase();
  if (!normalized) return undefined;
  return cities.find(option => option.label.toLocaleLowerCase() === normalized)
    ?? (state ? cities.find(option => option.city.toLocaleLowerCase() === normalized && option.state.toLocaleLowerCase() === state.toLocaleLowerCase()) : undefined)
    ?? (cities.filter(option => option.city.toLocaleLowerCase() === normalized).length === 1
      ? cities.find(option => option.city.toLocaleLowerCase() === normalized)
      : undefined);
}

function useCityOptions() {
  const [cities, setCities] = useState<CityOption[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    loadIndiaCities().then(options => { if (active) setCities(options); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);
  return { cities, loading };
}

export function IndiaCityStateFields({
  cityName = 'city', stateName = 'state', cityDefaultValue = '', stateDefaultValue = '',
  cityLabel = 'City', stateLabel = 'State', required = false,
}: {
  cityName?: string; stateName?: string; cityDefaultValue?: string; stateDefaultValue?: string;
  cityLabel?: string; stateLabel?: string; required?: boolean;
}) {
  const { cities, loading } = useCityOptions();
  const [listId] = useState(() => `india-cities-${++nextListId}`);
  const initialCity = findCity(cityDefaultValue, cities, stateDefaultValue);
  const [input, setInput] = useState(initialCity?.label ?? cityDefaultValue);
  const [chosen, setChosen] = useState<CityOption | undefined>(initialCity);
  const stateValue = chosen?.state ?? (input === cityDefaultValue ? stateDefaultValue : '');

  useEffect(() => {
    const match = findCity(cityDefaultValue, cities, stateDefaultValue);
    if (match) { setInput(match.label); setChosen(match); }
  }, [cities, cityDefaultValue, stateDefaultValue]);

  function change(value: string) {
    setInput(value);
    const match = findCity(value, cities, stateDefaultValue);
    setChosen(match);
  }

  return <>
    <label className="field"><span>{cityLabel}</span><input list={listId} value={input} onChange={event => change(event.target.value)} placeholder={loading ? 'Loading Indian cities…' : 'Type or choose a city'} autoComplete="off" required={required}/></label>
    <datalist id={listId}>{cities.map(option => <option key={option.label} value={option.label}/>)}</datalist>
    <label className="field"><span>{stateLabel}</span><input value={stateValue} readOnly placeholder="Select a city to fill state"/></label>
    <input type="hidden" name={cityName} value={chosen?.city ?? input}/>
    <input type="hidden" name={stateName} value={stateValue}/>
  </>;
}

export function IndiaLocationField({
  name = 'location', defaultValue = '', label = 'Client location', required = false,
}: { name?: string; defaultValue?: string; label?: string; required?: boolean }) {
  const { cities, loading } = useCityOptions();
  const [listId] = useState(() => `india-city-locations-${++nextListId}`);
  const initial = findCity(defaultValue, cities);
  const [input, setInput] = useState(initial?.label ?? defaultValue);
  const [chosen, setChosen] = useState<CityOption | undefined>(initial);

  useEffect(() => {
    const match = findCity(defaultValue, cities);
    if (match) { setInput(match.label); setChosen(match); }
  }, [cities, defaultValue]);

  function change(value: string) {
    setInput(value);
    setChosen(findCity(value, cities));
  }

  return <>
    <label className="field"><span>{label}</span><input list={listId} value={input} onChange={event => change(event.target.value)} placeholder={loading ? 'Loading Indian cities…' : 'Type or choose a city'} autoComplete="off" required={required}/></label>
    <datalist id={listId}>{cities.map(option => <option key={option.label} value={option.label}/>)}</datalist>
    <input type="hidden" name={name} value={chosen?.label ?? input}/>
  </>;
}
