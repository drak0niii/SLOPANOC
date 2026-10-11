import { useState, useLayoutEffect, type ReactNode } from 'react';
import { LandingHeader } from '../landing/LandingHeader';

/** Existing site navigation with an operations-width body beneath its fixed header. */
export function ResourcesShell({ children }: { children: ReactNode }) {
    const [activeMenu, setActiveMenu] = useState('');
    useLayoutEffect(() => {
        document.documentElement.classList.add('resources-dark');
        return () => document.documentElement.classList.remove('resources-dark');
    }, []);
    return <div data-shell="resources" className="min-h-dvh w-full bg-bg text-primary">
        <LandingHeader activeMenu={activeMenu} onActiveMenuChange={setActiveMenu} />
        <div className="mx-auto w-full max-w-[1920px] pt-16">{children}</div>
    </div>;
}
