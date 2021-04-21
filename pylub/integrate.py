"""
MIT License

Copyright 2021 Hannes Holey

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


import numpy as np
from mpi4py import MPI

from pylub.field import VectorField
from pylub.stress import SymStressField2D, SymStressField3D, SymStochStressField3D
from pylub.geometry import GapHeight
from pylub.material import Material


class ConservedField(VectorField):

    def __init__(self, disc, bc, geometry, material, numerics, q_init=None, t_init=None):
        """
        This class contains the field of conserved variable densities (rho, jx, jy),
        and the methods to update them. Derived from VectorField.

        Parameters
        ----------
        disc : dict
            Discretization parameters.
        bc : dict
            Boundary condition parameters.
        geometry : dict
            Geometry parameters.
        material : dict
            Material parameters.
        numerics : dict
            numerics parameters
        q_init : np.array
            Inital field in case of a restart (the default is None)
        t_init : tuple
            Initial time and time step in case of a restart (the default is None).

        """

        super().__init__(disc)

        self.disc = disc
        self.bc = bc
        self.material = material
        self.numerics = numerics

        # initialize gap height field
        self.height = GapHeight(disc, geometry)

        # intialize field and time
        if q_init is not None:
            ng = self.disc["nghost"]
            self.field[:, ng:-ng, ng:-ng] = q_init[:, ng:-ng, ng:-ng]
            self.fill_ghost_buffer()
            self.time = t_init[0]
            self.dt = t_init[1]
        else:
            self.field[0] = material['rho0']
            self.time = 0.
            self.dt = numerics["dt"]

        self.viscous_stress = SymStressField2D(disc, geometry, material)
        self.upper_stress = SymStressField3D(disc, geometry, material)
        self.lower_stress = SymStressField3D(disc, geometry, material)

        if bool(self.numerics["fluctuating"]):
            self.stoch_stress = SymStochStressField3D(disc, geometry, material)

    @property
    def mass(self):
        area = self.disc["dx"] * self.disc["dy"]
        local_mass = np.sum(self.inner[0] * self.height.inner[0] * area)
        recvbuf = np.empty(1, dtype=float)
        self.comm.Allreduce(local_mass, recvbuf, op=MPI.SUM)

        return recvbuf[0]

    @property
    def vSound(self):
        local_vSound = Material(self.material).eos_sound_speed(self.inner[0])
        recvbuf = np.empty(1, dtype=float)
        self.comm.Allreduce(local_vSound, recvbuf, op=MPI.MAX)

        return recvbuf[0]

    @property
    def vmax(self):
        local_vmax = np.amax(np.sqrt(self.inner[1]**2 + self.inner[2]**2) / self.inner[0])
        recvbuf = np.empty(1, dtype=float)
        self.comm.Allreduce(local_vmax, recvbuf, op=MPI.MAX)

        return recvbuf[0]

    def update(self, i):
        """
        Wrapper function for the explicit time update of the solution field.

        Parameters
        ----------
        i : int
            time step

        """

        integrator = self.numerics["integrator"]

        # MacCormack forward backward
        if integrator == "MC" or integrator == "MC_fb":
            try:
                self.mac_cormack(0)
            except NotImplementedError:
                print("MacCormack scheme not implemented. Exit!")
                quit()

        # Mac Cormack backward forward
        elif integrator == "MC_bf":
            try:
                self.mac_cormack(1)
            except NotImplementedError:
                print("MacCormack scheme not implemented. Exit!")
                quit()

        # MacCormack alternating
        elif integrator == "MC_alt":
            try:
                self.mac_cormack(i % 2)
            except NotImplementedError:
                print("MacCormack scheme not implemented. Exit!")
                quit()

        # Richtmyer two-step Lax-Wendroff
        elif integrator == "LW":
            try:
                self.richtmyer()
            except NotImplementedError:
                print("Lax-Wendroff scheme not implemented. Exit!")
                quit()

        # 3rd order Runge-Kutta
        elif integrator == "RK3":
            try:
                self.runge_kutta3()
            except NotImplementedError:
                print("Runge-Kutta 3 scheme not implemented. Exit!")
                quit()

    def mac_cormack(self, switch):
        """
        Explicit time update using MacCormack's predictor corrector scheme with source term.
        Field is updated in place.

        Parameters
        ----------
        switch : int
            Determine if predictor step is forwards (0) or backwards (1).

        """

        dx = self.disc["dx"]
        dy = self.disc["dy"]

        if switch == 0:
            directions = [-1, 1]
        elif switch == 1:
            directions = [1, -1]

        q0 = self.field.copy()

        for dir in directions:

            fX, fY = self.predictor_corrector(self.field, self.height.field, dir)
            src = self.get_source(self.field, self.height.field)

            self.field = self.field - self.dt * (fX / dx + fY / dy - src)

        self.field = 0.5 * (self.field + q0)
        self.fill_ghost_buffer()

        self.post_integrate(q0)

    def richtmyer(self):
        """
        Explicit time update using Richtmyer's two-step scheme with source term.
        Field is updated in place.

        """

        q0 = self.field.copy()

        # Step 1
        self.lax_step()
        # Step 2
        self.leap_frog(q0)

        self.fill_ghost_buffer()

        self.post_integrate(q0)

    def runge_kutta3(self):

        if bool(self.numerics["fluctuating"]):
            W_A = np.random.normal(size=(3, 3) + self.field.shape[1:])
            W_B = np.random.normal(size=(3, 3) + self.field.shape[1:])

        q0 = self.field.copy()

        for stage in np.arange(1, 4):

            dX, dY = self.diffusiveCD()
            fX, fY = self.hyperbolicTVD()
            src = self.get_source(self.field, self.height.field)

            if bool(self.numerics["fluctuating"]):
                self.stoch_stress.set(W_A, W_B, self.height.field, self.dt, stage)
                sX, sY = self.stochasticFlux()
                tmp = self.field - fX - fY + dX + dY + sX + sY + self.dt * src
            else:
                tmp = self.field - fX - fY + dX + dY + self.dt * src

            if stage == 1:
                self.field = tmp
                self.fill_ghost_buffer()
            elif stage == 2:
                self.field = 3 / 4 * q0 + 1 / 4 * tmp
                self.fill_ghost_buffer()
            elif stage == 3:
                self.field = 1 / 3 * q0 + 2 / 3 * tmp
                self.fill_ghost_buffer()

        self.post_integrate(q0)

    def post_integrate(self, q0):
        """
        Compute relative change of solution after time update.
        Update time, and, if adaptive time-stepping is used, also the time step.

        Parameters
        ----------
        q0 : np.array
            Copy of the solution field from the previous step

        """
        min_dist = min(self.disc["dx"], self.disc["dy"])
        ng = self.disc["nghost"]

        self.time += self.dt

        if bool(self.numerics["adaptive"]):
            CFL = self.numerics["C"]
            self.dt = CFL * min_dist / (self.vmax + self.vSound)
        else:
            CFL = self.dt * (self.vmax + self.vSound) / min_dist

        diff = np.empty(1)
        denom = np.empty(1)

        local_diff = np.sum((self.inner[0] - q0[0, ng:-ng, ng:-ng])**2)
        local_denom = np.sum(q0[0, ng:-ng, ng:-ng]**2)

        self.comm.Allreduce(local_diff, diff, op=MPI.SUM)
        self.comm.Allreduce(local_denom, denom, op=MPI.SUM)

        self.eps = np.sqrt(diff[0] / denom[0]) / CFL

    def fill_ghost_buffer(self):
        """
        Communicate results from adjacent domains to ghost buffers.
        Fill ghost cells at the boundary according to the applied BCs.

        """

        x0 = self.bc["x0"]
        x1 = self.bc["x1"]
        y0 = self.bc["y0"]
        y1 = self.bc["y1"]

        (ls, ld), (rs, rd), (bs, bd), (ts, td) = self.get_neighbors()

        ng = self.disc["nghost"]
        ngt = 2 * ng

        # Send to left, receive from right
        recvbuf = np.ascontiguousarray(self.field[:, -ng:, :])
        self.comm.Sendrecv(np.ascontiguousarray(self.field[:, ng:ngt, :]), ld, recvbuf=recvbuf, source=ls)
        # fill ghost buffer, adjust for non-periodic BCs
        if ls >= 0:
            self.field[:, -ng:, :] = recvbuf
        else:
            self.field[x1 == "D", -1, :] = 2. * self.bc["rhox1"] - self.field[x1 == "D", -2, :]
            self.field[x1 == "N", -1, :] = self.field[x1 == "N", -2, :]

        # Send to right, receive from left
        recvbuf = np.ascontiguousarray(self.field[:, :ng, :])
        self.comm.Sendrecv(np.ascontiguousarray(self.field[:, -ngt:-ng, :]), rd, recvbuf=recvbuf, source=rs)
        # fill ghost buffer, adjust for non-periodic BCs
        if rs >= 0:
            self.field[:, :ng, :] = recvbuf
        else:
            self.field[x0 == "D", 0, :] = 2. * self.bc["rhox0"] - self.field[x0 == "D", 1, :]
            self.field[x0 == "N", 0, :] = self.field[x0 == "N", 1, :]

        # Send to bottom, receive from top
        recvbuf = np.ascontiguousarray(self.field[:, :, -ng:])
        self.comm.Sendrecv(np.ascontiguousarray(self.field[:, :, ng:ngt]), bd, recvbuf=recvbuf, source=bs)
        # fill ghost buffer, adjust for non-periodic BCs
        if bs >= 0:
            self.field[:, :, -ng:] = recvbuf
        else:
            self.field[y1 == "D", :, -1] = 2. * self.bc["rhoy1"] - self.field[y1 == "D", :, -2]
            self.field[y1 == "N", :, -1] = self.field[y1 == "N", :, -2]

        # Send to top, receive from bottom
        recvbuf = np.ascontiguousarray(self.field[:, :, :ng])
        self.comm.Sendrecv(np.ascontiguousarray(self.field[:, :, -ngt:-ng]), td, recvbuf=recvbuf, source=ts)
        # fill ghost buffer, adjust for non-periodic BCs
        if ts >= 0:
            self.field[:, :, :ng] = recvbuf
        else:
            self.field[y0 == "D", :, 0] = 2. * self.bc["rhoy0"] - self.field[y0 == "D", :, 1]
            self.field[y0 == "N", :, 0] = self.field[y0 == "N", :, 1]

    def get_source(self, q, h):
        """
        Compute the source term from the unknown and gap height.

        Parameters
        ----------
        q : np.array
            Solution field.
        h : np.array
            Gap height (and gradients).

        Returns
        -------
        np.array
            Source term

        """

        self.viscous_stress.set(q, h)
        self.upper_stress.set(q, h, "top")
        self.lower_stress.set(q, h, "bottom")

        out = np.zeros_like(q)

        # origin bottom, U_top = 0, U_bottom = U
        out[0] = (-q[1] * h[1] - q[2] * h[2]) / h[0]

        if bool(self.numerics["stokes"]):
            out[1] = ((q[1] * q[1] / q[0] + self.viscous_stress.field[0] - self.upper_stress.field[0]) * h[1] +
                      (q[1] * q[2] / q[0] + self.viscous_stress.field[2] - self.upper_stress.field[5]) * h[2] +
                      self.upper_stress.field[4] - self.lower_stress.field[4]) / h[0]

            out[2] = ((q[2] * q[1] / q[0] + self.viscous_stress.field[2] - self.upper_stress.field[5]) * h[1] +
                      (q[2] * q[2] / q[0] + self.viscous_stress.field[1] - self.upper_stress.field[1]) * h[2] +
                      self.upper_stress.field[3] - self.lower_stress.field[3]) / h[0]
        else:
            out[1] = ((self.viscous_stress.field[0] - self.upper_stress.field[0]) * h[1] +
                      (self.viscous_stress.field[2] - self.upper_stress.field[5]) * h[2] +
                      self.upper_stress.field[4] - self.lower_stress.field[4]) / h[0]

            out[2] = ((self.viscous_stress.field[2] - self.upper_stress.field[5]) * h[1] +
                      (self.viscous_stress.field[1] - self.upper_stress.field[1]) * h[2] +
                      self.upper_stress.field[3] - self.lower_stress.field[3]) / h[0]

        return out

    def predictor_corrector(self, q, h, dir):
        """
        Compute predictor/corrector fluxes fro MacCormack's method.

        Parameters
        ----------
        q : np.array
            Solution field.
        h : np.array
            Gap height (and gradients).
        dir : int
            Direction of the substep (-1: forwards / 1: backwards differencing)

        Returns
        -------
        tuple
            Total numerical flux in x- and y-directions respectively.

        """

        Fx = self.hyperbolicFlux(q, 1) - self.diffusiveFlux(q, h, 1)
        Fy = self.hyperbolicFlux(q, 2) - self.diffusiveFlux(q, h, 2)

        flux_x = -dir * (np.roll(Fx, dir, axis=1) - Fx)
        flux_y = -dir * (np.roll(Fy, dir, axis=2) - Fy)

        return flux_x, flux_y

    def hyperbolicFlux(self, q, ax):
        """Compute hyperbolic flux.

        Parameters
        ----------
        q : np.array
            Solution field.
        ax : int
            Axis parameter, 1 == x and 2 == y

        Returns
        -------
        np.array
            Hyperbolic flux field.

        """

        p = Material(self.material).eos_pressure(q[0])
        inertialess = bool(self.numerics["stokes"])

        F = np.zeros_like(q)
        if ax == 1:
            if inertialess:
                F[0] = q[1]
                F[1] = p
            else:
                F[0] = q[1]
                F[1] = q[1] * q[1] / q[0] + p
                F[2] = q[2] * q[1] / q[0]

        elif ax == 2:
            if inertialess:
                F[0] = q[2]
                F[2] = p
            else:
                F[0] = q[2]
                F[1] = q[1] * q[2] / q[0]
                F[2] = q[2] * q[2] / q[0] + p

        return F

    def diffusiveFlux(self, q, h, ax):
        """Compute diffusive flux.

        Parameters
        ----------
        q : np.array
            Solution field.
        h : np.array
            Gap height (and gradients).
        ax : int
            Axis parameter, 1 == x and 2 == y

        Returns
        -------
        np.array
            Diffusive flux field

        """

        self.viscous_stress.set(q, h)

        D = np.zeros_like(q)
        if ax == 1:
            D[1] = self.viscous_stress.field[0]
            D[2] = self.viscous_stress.field[2]

        elif ax == 2:
            D[1] = self.viscous_stress.field[2]
            D[2] = self.viscous_stress.field[1]

        return D

    def lax_step(self):

        dx = self.disc["dx"]
        dy = self.disc["dy"]

        QS = self.edgeE
        QW = self.edgeN
        QN = np.roll(QS, -1, axis=2)
        QE = np.roll(QW, -1, axis=1)

        hS = self.height.edgeE
        hW = self.height.edgeN
        hN = np.roll(hS, -1, axis=2)
        hE = np.roll(hW, -1, axis=1)

        FS = self.hyperbolicFlux(QS, 1) - self.diffusiveFlux(QS, hS, 1)
        FW = self.hyperbolicFlux(QW, 2) - self.diffusiveFlux(QW, hW, 2)
        FN = self.hyperbolicFlux(QN, 1) - self.diffusiveFlux(QN, hN, 1)
        FE = self.hyperbolicFlux(QE, 2) - self.diffusiveFlux(QE, hE, 2)

        src = self.get_source(self.verticeNE, self.height.verticeNE)

        self.field = self.verticeNE - self.dt / 2. * ((FE - FW) / dx + (FN - FS) / dy - src)

    def leap_frog(self, q0):

        dx = self.disc["dx"]
        dy = self.disc["dy"]

        QE = self.edgeS
        QN = self.edgeW
        QW = np.roll(QE, 1, axis=1)
        QS = np.roll(QN, 1, axis=2)

        FE = self.hyperbolicFlux(QE, 1) - self.diffusiveFlux(QE, self.height.edgeE, 1)
        FN = self.hyperbolicFlux(QN, 2) - self.diffusiveFlux(QN, self.height.edgeN, 2)
        FW = self.hyperbolicFlux(QW, 1) - self.diffusiveFlux(QW, self.height.edgeW, 1)
        FS = self.hyperbolicFlux(QS, 2) - self.diffusiveFlux(QS, self.height.edgeS, 2)

        src = self.get_source(self.verticeSW, self.height.field)

        self.field = q0 - self.dt / dx * (FE - FW) - self.dt / dy * (FN - FS) + src * self.dt

    def cubicInterpolation(self, q, ax):

        # weights proposed by Bell et al., PRE 76 (2007)
        # a1 = (np.sqrt(7) + 1) / 4
        # a2 = (np.sqrt(7) - 1) / 4

        # weights from piecewise parabolic method (PPM)
        # Collela and Woodward, J. Comp. Phys. 54 (1984)
        a1 = 7 / 12
        a2 = 1 / 12

        Q = a1 * (q + np.roll(q, -1, axis=ax)) - a2 * (np.roll(q, 1, axis=ax) + np.roll(q, -2, axis=ax))

        return Q

    def hyperbolicTVD(self):

        dx = self.disc["dx"]
        dy = self.disc["dy"]

        Qx = self.cubicInterpolation(self.field, 1)
        # Px = EquationOfState(self.material).isoT_pressure(Qx[0])

        Qy = self.cubicInterpolation(self.field, 2)
        # Py = EquationOfState(self.material).isoT_pressure(Qy[0])

        Fx = self.hyperbolicFlux(Qx, 1)
        Fy = self.hyperbolicFlux(Qy, 2)

        flux_x = Fx - np.roll(Fx, 1, axis=1)
        flux_y = Fy - np.roll(Fy, 1, axis=2)

        return self.dt / dx * flux_x, self.dt / dy * flux_y

    def hyperbolicCD(self, q, p, dt, ax):

        dx = self.disc["dx"]
        dy = self.disc["dy"]

        F = self.hyperbolicFlux(q.field, ax)

        flux = np.roll(F, -1, axis=ax) - np.roll(F, 1, axis=ax)

        if ax == 1:
            dx = self.disc["dx"]
            return dt / (2 * dx) * flux

        elif ax == 2:
            dy = self.disc["dy"]
            return dt / (2 * dy) * flux

    def diffusiveCD(self):

        dx = self.disc["dx"]
        dy = self.disc["dy"]

        Dx = self.diffusiveFlux(self.field, self.height.field, 1)
        Dy = self.diffusiveFlux(self.field, self.height.field, 2)

        flux_x = np.roll(Dx, -1, axis=1) - np.roll(Dx, 1, axis=1)
        flux_y = np.roll(Dy, -1, axis=2) - np.roll(Dy, 1, axis=2)

        return self.dt / (2 * dx) * flux_x, self.dt / (2 * dy) * flux_y

    def stochasticFlux(self):

        dx = self.disc["dx"]
        dy = self.disc["dy"]

        Sx_E = np.zeros((3,) + self.field.shape[1:])
        Sx_W = np.zeros((3,) + self.field.shape[1:])
        Sy_N = np.zeros((3,) + self.field.shape[1:])
        Sy_S = np.zeros((3,) + self.field.shape[1:])

        corrX = dx / self.height.field[0]
        corrY = dy / self.height.field[0]

        # corrX = corrY = 1.

        Sx_E[1] = self.stoch_stress.field[0]
        Sx_E[2] = self.stoch_stress.field[5]
        Sx_W[1] = np.roll(self.stoch_stress.field[0], 1, axis=0)
        Sx_W[2] = np.roll(self.stoch_stress.field[5], 1, axis=0)

        Sy_N[1] = self.stoch_stress.field[5]
        Sy_N[2] = self.stoch_stress.field[1]
        Sy_S[1] = np.roll(self.stoch_stress.field[5], 1, axis=1)
        Sy_S[2] = np.roll(self.stoch_stress.field[1], 1, axis=1)

        return self.dt / dx * (Sx_E - Sx_W) * corrX, self.dt / dy * (Sy_N - Sy_S) * corrY
